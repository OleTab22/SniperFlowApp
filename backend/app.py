from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
import json

router = APIRouter()

@router.get("/v1/fred/latest")
async def v1_fred_latest(series: str = "DFII10"):
    """Return latest value for a FRED series (e.g., DFII10, DGS10)."""
    try:
        data = await fred_latest(series)
        if not data:
            raise HTTPException(status_code=404, detail="No data")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"fred/latest: {e}")
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg2
import time
import asyncio
import httpx
import logging
from datetime import datetime, timedelta, timezone
import pytz
from typing import Optional, Tuple, Dict, Any, List
import csv
from io import StringIO
import math
import lzma
import xml.etree.ElementTree as ET
from dateutil import parser as dateparser
import struct

NY = pytz.timezone("America/New_York")
ALPHA_BASE = "https://www.alphavantage.co/query"
ALPHA_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
TWELVE_BASE = "https://api.twelvedata.com/time_series"
TWELVE_KEY = os.getenv("TWELVEDATA_API_KEY")
PRICE_SOURCE = os.getenv("PRICE_SOURCE", "auto").lower()  # auto|twelvedata|dukascopy|stooq|alpha
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_KEY = os.getenv("FRED_API_KEY")
GOLDAPI_BASE = "https://www.goldapi.io/api"
GOLDAPI_KEY = os.getenv("GOLDAPI_KEY")
ENABLE_GOLDAPI = (os.getenv("ENABLE_GOLDAPI", "false").lower() == "true")

_client: Optional[httpx.AsyncClient] = None
_cache: Dict[Tuple[str, str], Tuple[int, Any]] = {}

# Lightweight in-memory signals store and websocket client registry
_signals_store: list[dict] = []
_signal_ws_clients: set[WebSocket] = set()

# Institutional-lite microstructure engine
try:
    from .microstructure import MicroEngine
    _micro_engine = MicroEngine(win_secs=20, ofi_decay=0.95)
    _last_pro_signal: Optional[Dict[str, Any]] = None
    _micro_enabled = True
except Exception as e:
    logging.warning(f"Microstructure engine unavailable: {e}")
    _micro_engine = None
    _last_pro_signal = None
    _micro_enabled = False


# ----- Provider circuit breakers -----
def _td_block_key():
    return ("td_blocked", "X")

def _is_td_blocked() -> bool:
    hit = _cache.get(_td_block_key())
    if not hit:
        return False
    return now_utc_ms() < hit[0]

def _block_td_until_reset():
    try:
        now = datetime.now(timezone.utc)
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        # small buffer (5 minutes)
        deadline = int(next_midnight.timestamp() * 1000) + 5 * 60 * 1000
        _cache[_td_block_key()] = (deadline, True)
    except Exception:
        # fallback 1 hour block
        _cache[_td_block_key()] = (now_utc_ms() + 60 * 60 * 1000, True)


# ----- Yahoo helpers (last-resort; strong caching/backoff) -----
YF_BASE_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
YF_BASE_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"

def _yf_block_key():
    return ("yf_blocked", "X")

def _is_yf_blocked() -> bool:
    hit = _cache.get(_yf_block_key())
    return bool(hit and now_utc_ms() < hit[0])

def _block_yf(minutes: int = 45):
    _cache[_yf_block_key()] = (now_utc_ms() + minutes * 60 * 1000, True)


# ----- GoldAPI circuit breaker -----
def _gapi_block_key():
    return ("gapi_blocked", "X")

def _is_gapi_blocked() -> bool:
    hit = _cache.get(_gapi_block_key())
    return bool(hit and now_utc_ms() < hit[0])

def _block_gapi(minutes: int = 10):
    _cache[_gapi_block_key()] = (now_utc_ms() + minutes * 60 * 1000, True)


# ----- Day cache helpers -----
def _next_utc_midnight_ms(buffer_min: int = 5) -> int:
    now = datetime.now(timezone.utc)
    nxt = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int(nxt.timestamp() * 1000) + buffer_min * 60 * 1000

async def levels_today_cached(symbol: str, candles: Optional[list] = None) -> Optional[dict]:
    """Compute DO/PDH/PDL once per UTC day and cache until next midnight."""
    # v2: bump cache key to recompute after logic changes
    key = ("levels_today_v2", symbol.upper())
    hit = _cache.get(key)
    now = now_utc_ms()
    if hit and now < hit[0]:
        return hit[1]
    try:
        # Preferred PDH/PDL from GoldAPI daily; fallback to Stooq daily
        try:
            prev_levels = await goldapi_pdh_pdl(symbol)
            if prev_levels.get("PDH") is None or prev_levels.get("PDL") is None:
                raise RuntimeError("incomplete goldapi pdh/pdl")
        except Exception:
            prev_levels = await stooq_daily_pdh_pdl(symbol)
        # Final fallback: derive PDH/PDL from candles if still missing
        if prev_levels.get("PDH") is None or prev_levels.get("PDL") is None:
            try:
                local_candles = candles
                if local_candles is None:
                    local_candles, _ = await get_candles(symbol)
                pv = _compute_prev_day_levels_strict_utc(local_candles, max_lookback_days=3)
                if pv.get("PDH") is not None and pv.get("PDL") is not None:
                    prev_levels = pv
            except Exception:
                pass
        # DO priority: GoldAPI open -> candles -> Stooq daily open
        do_price = None
        try:
            gq = await cached_goldapi_quote(symbol)
            do_price = gq.get("open") if gq else None
        except Exception:
            pass
        if do_price is None:
            try:
                local_candles = candles
                if local_candles is None:
                    local_candles, _last = await get_candles(symbol)
                now_utc = datetime.now(timezone.utc)
                today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                next_midnight = today_midnight + timedelta(days=1)
                today_window = filter_candles(local_candles, to_utc_ms(today_midnight), to_utc_ms(next_midnight))
                do_price = compute_levels_for_window(today_window)["DO"]
            except Exception:
                pass
        if do_price is None:
            try:
                do_price = await stooq_daily_open_today(symbol)
            except Exception:
                pass
        payload = {"DO": do_price, "PDH": prev_levels.get("PDH"), "PDL": prev_levels.get("PDL")}
        _cache[key] = (_next_utc_midnight_ms(), payload)
        return payload
    except Exception:
        return None

async def _with_backoff_http(fn, tries: int = 3, base: float = 0.6):
    for i in range(tries):
        try:
            return await fn()
        except Exception:
            await asyncio.sleep(base * (2 ** i))
    return await fn()

def _cache_get(key: Tuple[str, str], ttl_ms: int):
    hit = _cache.get(key)
    if hit and (now_utc_ms() - hit[0]) < ttl_ms:
        return hit[1]
    return None

def _cache_put(key: Tuple[str, str], val: Any):
    _cache[key] = (now_utc_ms(), val)

def _yf_symbol_xau() -> list[str]:
    # Prefer spot XAUUSD=X, then GC=F (futures)
    return ["XAUUSD=X", "GC=F"]

async def yahoo_last(sym: str) -> Optional[float]:
    key = ("yf_last", sym)
    hit = _cache_get(key, ttl_ms=30_000)
    if hit is not None:
        return hit
    if _is_yf_blocked():
        return None
    async def fetch():
        r = await _client.get(YF_BASE_QUOTE, params={"symbols": sym}, timeout=10)
        if r.status_code == 429:
            _block_yf()
            return None
        r.raise_for_status()
        j = r.json()
        res = (j or {}).get("quoteResponse", {}).get("result", [])
        if not res:
            return None
        v = res[0].get("regularMarketPrice")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    val = await _with_backoff_http(fetch)
    _cache_put(key, val)
    return val

async def yahoo_quote_pct(sym: str) -> Optional[float]:
    key = ("yf_pct", sym)
    hit = _cache_get(key, ttl_ms=30_000)
    if hit is not None:
        return hit
    if _is_yf_blocked():
        return None
    async def fetch():
        r = await _client.get(YF_BASE_QUOTE, params={"symbols": sym}, timeout=10)
        if r.status_code == 429:
            _block_yf()
            return None
        r.raise_for_status()
        j = r.json()
        res = (j or {}).get("quoteResponse", {}).get("result", [])
        if not res:
            return None
        v = res[0].get("regularMarketChangePercent")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    val = await _with_backoff_http(fetch)
    _cache_put(key, val)
    return val

async def yahoo_series_5m(sym: str) -> Optional[Tuple[list, float]]:
    key = ("yf_series5m", sym)
    hit = _cache_get(key, ttl_ms=30_000)
    if hit is not None:
        return hit
    if _is_yf_blocked():
        return None
    async def fetch():
        url = f"{YF_BASE_CHART}{sym}"
        params = {"interval": "5m", "range": "1d", "includePrePost": "false"}
        r = await _client.get(url, params=params, timeout=12)
        if r.status_code == 429:
            _block_yf()
            return None
        r.raise_for_status()
        j = r.json()
        r0 = (j or {}).get("chart", {}).get("result", [])
        if not r0:
            return None
        r1 = r0[0]
        ts = r1.get("timestamp", [])
        ind = (r1.get("indicators", {}) or {}).get("quote", [{}])[0]
        opens = ind.get("open", [])
        highs = ind.get("high", [])
        lows = ind.get("low", [])
        closes = ind.get("close", [])
        candles = []
        for i in range(min(len(ts), len(opens), len(highs), len(lows), len(closes))):
            try:
                tms = int(ts[i]) * 1000
                o = float(opens[i]) if opens[i] is not None else None
                h = float(highs[i]) if highs[i] is not None else None
                l = float(lows[i]) if lows[i] is not None else None
                c = float(closes[i]) if closes[i] is not None else None
                if None in (o, h, l, c):
                    continue
                candles.append({"t": tms, "o": o, "h": h, "l": l, "c": c})
            except Exception:
                continue
        if not candles:
            return None
        last = candles[-1]["c"]
        return candles, last
    val = await _with_backoff_http(fetch)
    if val is not None:
        _cache_put(key, val)
    return val


def now_utc_ms() -> int:
    return int(time.time() * 1000)


def to_utc_ms(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def ny_now() -> datetime:
    return datetime.now(tz=NY)


def session_day_anchor(ny_dt: datetime) -> datetime:
    # 5pm ET session anchor (rollover)
    five_pm = ny_dt.replace(hour=17, minute=0, second=0, microsecond=0)
    return five_pm if ny_dt >= five_pm else (five_pm - timedelta(days=1))


def make_window(ny_anchor: datetime, start_hm: Tuple[int, int], end_hm: Tuple[int, int]) -> Tuple[datetime, datetime]:
    s = ny_anchor.replace(hour=start_hm[0], minute=start_hm[1])
    e = ny_anchor.replace(hour=end_hm[0], minute=end_hm[1])
    if e <= s:
        e = e + timedelta(days=1)
    return s, e


def filter_candles(candles, start_ms: int, end_ms: int):
    return [c for c in candles if start_ms <= c["t"] < end_ms]


def compute_levels_for_window(candles_window):
    if not candles_window:
        return {"DO": None, "PDH": None, "PDL": None}
    ordered = sorted(candles_window, key=lambda x: x["t"])  # oldest first
    first = ordered[0]
    highs = [c["h"] for c in ordered if c["h"] is not None]
    lows = [c["l"] for c in ordered if c["l"] is not None]
    high = max(highs) if highs else None
    low = min(lows) if lows else None
    return {"DO": first["o"], "PDH": high, "PDL": low}


def _compute_prev_day_levels_strict_utc(candles, max_lookback_days: int = 3) -> dict:
    """Return PDH/PDL from the most recent prior UTC day that has bars.
       Looks back up to max_lookback_days (weekends/holidays)."""
    now_utc = datetime.now(timezone.utc)
    today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    for k in range(1, max(1, max_lookback_days) + 1):
        start = today_midnight - timedelta(days=k)
        end = today_midnight - timedelta(days=k - 1)
        prev_window = filter_candles(candles, to_utc_ms(start), to_utc_ms(end))
        lv = compute_levels_for_window(prev_window)
        if lv.get("PDH") is not None and lv.get("PDL") is not None:
            return {"PDH": lv["PDH"], "PDL": lv["PDL"]}
    return {"PDH": None, "PDL": None}


# Yahoo provider removed to avoid 429s and unsupported scraping


async def fetch_alpha(symbol: str):
    if symbol.upper() != "XAUUSD":
        raise RuntimeError("Alpha fallback only implemented for XAUUSD")
    if not ALPHA_KEY:
        raise RuntimeError("AlphaVantage key missing")
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": "XAU",
        "to_symbol": "USD",
        "interval": "5min",
        "outputsize": "compact",
        "apikey": ALPHA_KEY
    }
    r = await _client.get(ALPHA_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    # Alpha Vantage returns throttling/info in 'Note' or errors in 'Error Message'
    note = j.get("Note") if isinstance(j, dict) else None
    if note:
        raise RuntimeError(f"Alpha: {note}")
    err = j.get("Error Message") if isinstance(j, dict) else None
    if err:
        raise RuntimeError(f"Alpha: {err}")
    series = None
    for k in j.keys():
        if "Time Series" in k:
            series = j[k]
            break
    if not series:
        raise RuntimeError("Alpha: no series")
    # detect timezone if provided, default UTC
    tz_name = j.get("Meta Data", {}).get("6. Time Zone", "UTC")
    try:
        series_tz = pytz.timezone(tz_name)
    except Exception:
        series_tz = timezone.utc
    candles = []
    for ts_str, v in series.items():
        # timestamps like "2024-09-25 20:05:00"
        naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        if isinstance(series_tz, pytz.BaseTzInfo):
            aware = series_tz.localize(naive)
        else:
            aware = naive.replace(tzinfo=timezone.utc)
        candles.append({
            "t": to_utc_ms(aware),
            "o": float(v["1. open"]),
            "h": float(v["2. high"]),
            "l": float(v["3. low"]),
            "c": float(v["4. close"]),
        })
    candles.sort(key=lambda x: x["t"])
    if not candles:
        raise RuntimeError("Alpha: empty candles")
    last_price = candles[-1]["c"]
    return candles, last_price


async def fetch_alpha_fx_last(symbol: str) -> Optional[float]:
    """Alpha Vantage realtime FX last using CURRENCY_EXCHANGE_RATE. Cached.
       Docs: https://www.alphavantage.co/documentation/
    """
    if symbol.upper() != "XAUUSD":
        return None
    if not ALPHA_KEY:
        return None
    key = ("alpha_fx_last", symbol.upper())
    hit = _cache.get(key)
    if hit and (now_utc_ms() - hit[0]) < 10_000:
        return hit[1]
    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": "XAU",
        "to_currency": "USD",
        "apikey": ALPHA_KEY,
    }
    r = await _client.get(ALPHA_BASE, params=params, timeout=10)
    r.raise_for_status()
    j = r.json()
    note = j.get("Note") if isinstance(j, dict) else None
    if note:
        # throttle: serve stale if we have it
        if hit:
            return hit[1]
        return None
    err = j.get("Error Message") if isinstance(j, dict) else None
    if err:
        return None
    data = j.get("Realtime Currency Exchange Rate", {}) if isinstance(j, dict) else {}
    v = data.get("5. Exchange Rate") or data.get("Exchange Rate")
    try:
        val = float(v) if v is not None else None
        _cache[key] = (now_utc_ms(), val)
        return val
    except Exception:
        return None
async def fetch_alpha_global_quote_pct(symbol: str) -> Optional[float]:
    """Alpha Vantage GLOBAL_QUOTE percent change for equities/ETFs (e.g., SPY)."""
    if not ALPHA_KEY:
        return None
    key = ("alpha_pct", symbol.upper())
    hit = _cache.get(key)
    if hit and (now_utc_ms() - hit[0]) < 30_000:
        return hit[1]
    params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": ALPHA_KEY}
    r = await _client.get(ALPHA_BASE, params=params, timeout=10)
    r.raise_for_status()
    j = r.json()
    note = j.get("Note") if isinstance(j, dict) else None
    if note:
        return hit[1] if hit else None
    q = (j or {}).get("Global Quote", {})
    pct_str = q.get("10. change percent") or q.get("change percent")
    try:
        val = float(str(pct_str).replace("%", ""))
        _cache[key] = (now_utc_ms(), val)
        return val
    except Exception:
        return None

async def fetch_fred_pct(series_id: str) -> Optional[float]:
    s = await fetch_fred_series(series_id, max_points=2)
    try:
        vals = s["values"]
        if not vals:
            return None
        if len(vals) == 1:
            return 0.0
        a, b = vals[-2], vals[-1]
        if a == 0:
            return 0.0
        return (b - a) / a * 100.0
    except Exception:
        return None


def _build_synthetic_candles_from_last(last: float, bars: int = 60, interval_min: int = 5):
    """Produce a flat synthetic OHLC series ending now for UI continuity when providers fail."""
    end_ms = now_utc_ms()
    step_ms = interval_min * 60 * 1000
    candles = []
    for i in range(bars, 0, -1):
        t = end_ms - i * step_ms
        candles.append({"t": t, "o": last, "h": last, "l": last, "c": last})
    return candles


async def fetch_stooq(symbol: str):
    # Stooq free CSV, supports 5-minute bars for FX pairs
    pair = symbol.lower()
    if pair == "xauusd":
        ticker = "xauusd"
    else:
        ticker = pair
    url = f"https://stooq.com/q/d/l/?s={ticker}&i=5"
    r = await _client.get(url, timeout=15)
    r.raise_for_status()
    text = r.text
    # CSV header: date,time,open,high,low,close,volume
    f = StringIO(text)
    reader = csv.DictReader(f)
    candles = []
    for row in reader:
        try:
            dt_str = f"{row['date']} {row['time']}"
            naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            aware = naive.replace(tzinfo=timezone.utc)
            vol = None
            try:
                vol = float(row.get("volume")) if row.get("volume") not in (None, "") else None
            except Exception:
                vol = None
            candles.append({
                "t": to_utc_ms(aware),
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
                "v": vol,
            })
        except Exception:
            continue
    if not candles:
        raise RuntimeError("Stooq: empty series")
    candles.sort(key=lambda x: x["t"])
    last_price = candles[-1]["c"]
    return candles, last_price


async def fetch_stooq_daily(symbol: str) -> list[dict]:
    """Fetch Stooq daily OHLC CSV rows (oldest->newest). Columns: Date,Open,High,Low,Close,Volume"""
    pair = symbol.lower()
    ticker = "xauusd" if pair == "xauusd" else pair
    url = f"https://stooq.com/q/d/l/?s={ticker}&i=d"
    r = await _client.get(url, timeout=15)
    r.raise_for_status()
    text = r.text
    f = StringIO(text)
    # Stooq daily header typically capitalized; read case-insensitively
    reader = csv.DictReader(f)
    rows = []
    for row in reader:
        try:
            # Normalize keys
            rmap = {k.lower(): v for k, v in row.items()}
            dt_str = rmap.get("date") or rmap.get("d")
            o = float(rmap.get("open")) if rmap.get("open") not in (None, "") else None
            h = float(rmap.get("high")) if rmap.get("high") not in (None, "") else None
            l = float(rmap.get("low")) if rmap.get("low") not in (None, "") else None
            c = float(rmap.get("close")) if rmap.get("close") not in (None, "") else None
            if not dt_str:
                continue
            dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            rows.append({"date": dt.date(), "open": o, "high": h, "low": l, "close": c})
        except Exception:
            continue
    rows.sort(key=lambda x: x["date"])  # oldest -> newest
    return rows

async def stooq_daily_pdh_pdl(symbol: str) -> dict:
    """Return PDH/PDL from Stooq daily CSV (previous completed day)."""
    key = ("stooq_daily_lvls", symbol.upper())
    hit = _cache_get(key, ttl_ms=15 * 60 * 1000)
    if hit is not None:
        return hit
    rows = await fetch_stooq_daily(symbol)
    if len(rows) < 2:
        out = {"PDH": None, "PDL": None}
        _cache_put(key, out)
        return out
    # Previous day = last completed day before today
    today = datetime.now(timezone.utc).date()
    # Filter rows strictly before today
    hist = [r for r in rows if r["date"] < today]
    if len(hist) < 2:
        out = {"PDH": None, "PDL": None}
        _cache_put(key, out)
        return out
    prev_day = hist[-1]  # yesterday or last completed
    out = {"PDH": float(prev_day.get("high")) if prev_day.get("high") is not None else None,
           "PDL": float(prev_day.get("low")) if prev_day.get("low") is not None else None}
    _cache_put(key, out)
    return out

async def stooq_daily_open_today(symbol: str) -> Optional[float]:
    """Return today's daily open from Stooq if today's row exists; else None."""
    key = ("stooq_daily_open", symbol.upper())
    hit = _cache_get(key, ttl_ms=15 * 60 * 1000)
    if hit is not None:
        return hit
    rows = await fetch_stooq_daily(symbol)
    if not rows:
        _cache_put(key, None)
        return None
    today = datetime.now(timezone.utc).date()
    last = rows[-1]
    val = float(last.get("open")) if last.get("date") == today and last.get("open") is not None else None
    _cache_put(key, val)
    return val

async def fetch_twelvedata(symbol: str, outputsize: str = "60"):
    if not TWELVE_KEY:
        raise RuntimeError("TwelveData key missing")
    if _is_td_blocked():
        raise RuntimeError("TwelveData blocked until daily reset (quota exceeded)")
    # TwelveData expects symbols like XAU/USD
    sym = "XAU/USD" if symbol.upper() == "XAUUSD" else symbol
    params = {
        "symbol": sym,
        "interval": "5min",
        "outputsize": outputsize,  # default last ~5 hours; override where needed
        "apikey": TWELVE_KEY,
        "timezone": "UTC",
        "format": "JSON",
        "order": "ASC",
    }
    r = await _client.get(TWELVE_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    if "status" in j and j.get("status") == "error":
        # if quota error, trip breaker
        msg = j.get("message", "error")
        if "run out of API credits" in msg.lower():
            _block_td_until_reset()
        raise RuntimeError(f"TwelveData: {msg}")
    if "values" not in j:
        raise RuntimeError(f"TwelveData: {j.get('message') or 'no series'}")
    candles = []
    for v in j["values"]:
        try:
            dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            candles.append({
                "t": to_utc_ms(dt),
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"]),
            })
        except Exception:
            continue
    if not candles:
        raise RuntimeError("TwelveData: empty series")
    last_price = candles[-1]["c"]
    return candles, last_price


async def fetch_twelvedata_price(symbol: str) -> float:
    if not TWELVE_KEY:
        raise RuntimeError("TwelveData key missing")
    sym = "XAU/USD" if symbol.upper() == "XAUUSD" else symbol
    url = "https://api.twelvedata.com/price"
    r = await _client.get(url, params={"symbol": sym, "apikey": TWELVE_KEY}, timeout=10)
    r.raise_for_status()
    j = r.json()
    p = j.get("price")
    if p is None:
        raise RuntimeError(f"TwelveData price: {j}")
    return float(p)

async def fetch_twelvedata_quote(symbol: str) -> Dict[str, float]:
    if not TWELVE_KEY:
        raise RuntimeError("TwelveData key missing")
    if _is_td_blocked():
        raise RuntimeError("TwelveData blocked until daily reset (quota exceeded)")
    sym = "XAU/USD" if symbol.upper() == "XAUUSD" else symbol
    url = "https://api.twelvedata.com/quote"
    # Serve cached quote on transient errors / rate limits
    cache_key = ("td_quote_raw", symbol.upper())
    try:
        r = await _client.get(url, params={"symbol": sym, "apikey": TWELVE_KEY}, timeout=10)
    except Exception:
        hit = _cache_get(cache_key, ttl_ms=60_000)
        if hit is not None:
            return hit
        raise
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("status") == "error":
        msg = j.get("message", "error")
        if "run out of API credits" in msg.lower():
            _block_td_until_reset()
        hit = _cache_get(cache_key, ttl_ms=60_000)
        if hit is not None:
            return hit
        raise RuntimeError(f"TwelveData quote: {j}")
    # Some TD responses omit bid/ask and 'price' but include a 'close' field –
    # treat that as a valid last price instead of throwing.
    if all(k not in j for k in ("bid", "ask", "price", "close")):
        raise RuntimeError(f"TwelveData quote: {j}")
    def _to_f(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    out = {
        "bid": _to_f(j.get("bid")),
        "ask": _to_f(j.get("ask")),
        "last": _to_f(j.get("price") or j.get("close")),
    }
    _cache_put(cache_key, out)
    return out

async def cached_twelvedata_quote(symbol: str, ttl_sec: int = 60) -> Dict[str, float]:
    """Cache TD quote to avoid exceeding free-tier limits."""
    key = ("td_quote", symbol.upper())
    hit = _cache_get(key, ttl_ms=ttl_sec * 1000)
    if hit is not None:
        return hit
    q = await fetch_twelvedata_quote(symbol)
    _cache_put(key, q)
    return q

async def td_quote_pct(symbol: str, ttl_sec: int = 120) -> Optional[float]:
    """Percent change from Twelve Data quote (cached)."""
    if not TWELVE_KEY:
        return None
    if _is_td_blocked():
        return None
    key = ("td_pct", symbol.upper())
    hit = _cache.get(key)
    now = now_utc_ms()
    if hit and (now - hit[0] < ttl_sec * 1000):
        return hit[1]
    url = "https://api.twelvedata.com/quote"
    r = await _client.get(url, params={"symbol": symbol, "apikey": TWELVE_KEY}, timeout=10)
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("status") == "error":
        msg = j.get("message", "error")
        if "run out of API credits" in msg.lower():
            _block_td_until_reset()
        _cache[key] = (now, None)
        return None
    pct_raw = str(j.get("percent_change", "0")).replace("%", "")
    try:
        pct = float(pct_raw)
    except Exception:
        pct = None
    _cache[key] = (now, pct)
    return pct


async def fetch_goldapi_quote(symbol: str) -> Dict[str, float]:
    """GoldAPI realtime quote for XAU/USD. Returns {bid, ask, last, open}."""
    if not ENABLE_GOLDAPI:
        raise RuntimeError("GoldAPI disabled by ENABLE_GOLDAPI=false")
    if not GOLDAPI_KEY:
        raise RuntimeError("GoldAPI key missing")
    if _is_gapi_blocked():
        raise RuntimeError("GoldAPI blocked (backoff)")
    pair = symbol.upper()
    if pair == "XAUUSD":
        path = "XAU/USD"
    else:
        if len(pair) == 6:
            path = f"{pair[:3]}/{pair[3:]}"
        else:
            path = pair.replace("_", "/")
    url = f"{GOLDAPI_BASE}/{path}"
    headers = {"x-access-token": GOLDAPI_KEY, "Content-Type": "application/json"}
    r = await _client.get(url, headers=headers, timeout=10)
    # If forbidden or rate-limited, trip breaker and surface error
    if r.status_code in (403, 429):
        _block_gapi(minutes=15)
        raise RuntimeError(f"GoldAPI blocked: {r.status_code}")
    r.raise_for_status()
    j = r.json()
    def _to_f(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    out = {
        "bid": _to_f((j or {}).get("bid")),
        "ask": _to_f((j or {}).get("ask")),
        "last": _to_f((j or {}).get("price")),
        "open": _to_f((j or {}).get("open_price")),
        # GoldAPI provides 24h high/low fields (see docs); map if present
        "high": _to_f((j or {}).get("high_price")),
        "low": _to_f((j or {}).get("low_price")),
    }
    if all(out.get(k) is None for k in ("bid", "ask", "last", "open")):
        raise RuntimeError(f"GoldAPI: unexpected response {j}")
    return out

async def cached_goldapi_quote(symbol: str, ttl_sec: int = 60) -> Dict[str, float]:
    if not ENABLE_GOLDAPI:
        raise RuntimeError("GoldAPI disabled by ENABLE_GOLDAPI=false")
    key = ("gapi_quote", symbol.upper())
    hit = _cache.get(key)
    now = now_utc_ms()
    if hit and (now - hit[0] < ttl_sec * 1000):
        return hit[1]
    q = await fetch_goldapi_quote(symbol)
    _cache[key] = (now, q)
    return q


async def fetch_dukascopy(symbol: str, hours_back: int = 4):
    instr = symbol.upper()
    if instr != "XAUUSD":
        raise RuntimeError("Dukascopy fallback implemented for XAUUSD only")
    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ticks = []

    async def fetch_hour(dt_hour: datetime):
        base = "https://datafeed.dukascopy.com/datafeed"
        yyyy = dt_hour.year
        mm0 = dt_hour.month - 1  # zero-based month
        dd = dt_hour.day
        hh = dt_hour.hour
        url = f"{base}/{instr}/{yyyy:04d}/{mm0:02d}/{dd:02d}/{hh:02d}h_ticks.bi5"
        headers = {"Referer": "https://www.dukascopy.com/trading-tools/widgets/quotes/historical_data_feed"}
        async with httpx.AsyncClient(headers=headers) as _client:
            log.info("duka: fetching %s", url)
            r = await _client.get(url, timeout=20)
            r.raise_for_status()
            return _decompress_lzma(r.content)

    # Fetch newest to oldest so we can early stop when enough data
    for h in range(hours_back):
        dt_hr = now_utc - timedelta(hours=h)
        try:
            raw = await fetch_hour(dt_hr)
            if not raw:
                break
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                break
            else:
                continue
        except Exception:
            break
        # parse decompressed bytes
        rec = 20
        for i in range(0, len(raw) - (len(raw) % rec), rec):
            try:
                tms, ask_i, bid_i, _av_i, _bv_i = struct.unpack(">IIIII", raw[i:i+rec])
            except Exception:
                continue
            ts_ms = int(dt_hr.timestamp() * 1000) + int(tms)
            mid_i = (ask_i + bid_i) / 2.0
            price = None
            for s in (100000.0, 10000.0, 1000.0, 100.0, 10.0, 1.0):
                v = mid_i / s
                if 500.0 <= v <= 10000.0:
                    price = v
                    break
            if price is None:
                price = mid_i / 1000.0
            ticks.append((ts_ms, float(price)))

    if not ticks:
        raise RuntimeError("Dukascopy: empty series")
    ticks.sort(key=lambda x: x[0])

    # Aggregate to 1-minute OHLC
    by_minute: Dict[int, Dict[str, float]] = {}
    last_price = ticks[-1][1]
    for ts, price in ticks:
        m = (ts // 60000) * 60000
        b = by_minute.get(m)
        if not b:
            by_minute[m] = {"o": price, "h": price, "l": price, "c": price}
        else:
            b["h"] = max(b["h"], price)
            b["l"] = min(b["l"], price)
            b["c"] = price
    minutes_sorted = sorted(by_minute.items(), key=lambda x: x[0])
    candles = [{"t": m, "o": v["o"], "h": v["h"], "l": v["l"], "c": v["c"]} for m, v in minutes_sorted]
    return candles, last_price

async def get_candles(symbol: str):
    key = ("candles", symbol.upper())
    ts_payload = _cache.get(key)
    if ts_payload and (now_utc_ms() - ts_payload[0] < 15_000):
        return ts_payload[1]
    # Explicit source override via env for diagnostics
    if PRICE_SOURCE in ("twelvedata", "dukascopy", "stooq", "alpha"):
        if PRICE_SOURCE == "twelvedata":
            return await fetch_twelvedata(symbol)
        if PRICE_SOURCE == "dukascopy":
            return await fetch_dukascopy(symbol)
        if PRICE_SOURCE == "stooq":
            return await fetch_stooq(symbol)
        if PRICE_SOURCE == "alpha":
            # Be resilient even in forced alpha mode
            try:
                return await fetch_alpha(symbol)
            except Exception:
                # Fallback to Alpha realtime last + synthetic candles
                try:
                    last = await fetch_alpha_fx_last(symbol)
                    if last is not None:
                        return (_build_synthetic_candles_from_last(last), last)
                except Exception:
                    pass
                # Try Yahoo series before other paths
                try:
                    for s in _yf_symbol_xau():
                        ys = await yahoo_series_5m(s)
                        if ys:
                            return ys
                except Exception:
                    pass
                # Final fallbacks
                try:
                    return await fetch_stooq(symbol)
                except Exception:
                    return await fetch_dukascopy(symbol)

    # Auto strategy with sanity checks
    try:
            # For PDH/PDL to be reliable, ensure up to ~2 days of coverage so
            # previous UTC day is fully present even around midnight
            candles_td, last_td = await fetch_twelvedata(symbol, outputsize="600")
            last_sane = last_td
            try:
                q = await cached_twelvedata_quote(symbol)
                candidate = None
                if q.get("bid") and q.get("ask"):
                    candidate = (q["bid"] + q["ask"]) / 2.0
                elif q.get("last"):
                    candidate = q["last"]
                if candidate is not None:
                    c_last = candles_td[-1]["c"] if candles_td else candidate
                    if abs(candidate - c_last) <= 5.0:
                        last_sane = candidate
                    else:
                        try:
                            payload = await fetch_dukascopy(symbol)
                            _cache[key] = (now_utc_ms(), payload)
                            return payload
                        except Exception:
                            pass
            except Exception:
                pass
            payload = (candles_td, last_sane)
    except Exception as e_twelve:
            # Prefer Alpha first (official 5min FX intraday) to avoid long Dukascopy timeouts
        try:
            payload = await fetch_alpha(symbol)
        except Exception as e_alpha:
                # Try Yahoo 5m series as last-resort before other CSV/BI5 sources
            try:
                ys = _yf_symbol_xau()
                ypayload = None
                for s in ys:
                    ypayload = await yahoo_series_5m(s)
                    if ypayload:
                        break
                if ypayload:
                    payload = ypayload
                else:
                    raise RuntimeError("Yahoo series unavailable")
            except Exception as e_yahoo_series:
                # Try a realtime Alpha FX last and synthesize candles to keep the app usable
                try:
                    last = await fetch_alpha_fx_last(symbol)
                    if last is not None:
                        synth = _build_synthetic_candles_from_last(last)
                        payload = (synth, last)
                    else:
                        raise RuntimeError("Alpha FX last unavailable")
                except Exception as e_alpha_last:
                    # Try GoldAPI last/mid and synthesize candles to keep UI responsive
                    try:
                        gq = await cached_goldapi_quote("XAUUSD", ttl_sec=5)
                        last = None
                        if gq.get("last") is not None:
                            last = float(gq["last"])
                        elif gq.get("bid") is not None and gq.get("ask") is not None:
                            last = (float(gq["bid"]) + float(gq["ask"])) / 2.0
                        if last is not None:
                            synth = _build_synthetic_candles_from_last(last)
                            payload = (synth, last)
                        else:
                            raise RuntimeError("GoldAPI last unavailable")
                    except Exception as e_goldapi:
                        try:
                            payload = await fetch_stooq(symbol)
                        except Exception as e_stooq:
                            try:
                                # Use shorter timeout path already inside fetch_dukascopy; if still fails, bubble up
                                payload = await fetch_dukascopy(symbol)
                            except Exception as e_duka:
                                # As a final guard, serve emergency stale cache (up to 10 minutes) if available
                                stale = _cache.get(key)
                                if stale and (now_utc_ms() - stale[0]) < (10 * 60 * 1000):
                                    return stale[1]
                                err_msg = (f"TwelveData failed: {e_twelve}; Alpha failed: {e_alpha}; "
                                           f"Yahoo series failed: {e_yahoo_series}; Alpha FX last failed: {e_alpha_last}; "
                                           f"GoldAPI failed: {e_goldapi}; Stooq failed: {e_stooq}; Dukascopy failed: {e_duka}")
                                logging.critical(f"get_candles: all providers failed and no stale cache. Details: {err_msg}")
                                payload = ([], None)
    _cache[key] = (now_utc_ms(), payload)
    return payload


def build_sessions_windows(ny_anchor: datetime):
    # Major FX sessions within the session-day anchored at 17:00 ET
    sessions = {
        "sydney": ((17, 0), (2, 0)),
        "tokyo": ((19, 0), (4, 0)),
        "london": ((3, 0), (12, 0)),
        "newyork": ((8, 0), (17, 0)),
    }
    windows = {}
    for name, (sh, eh) in sessions.items():
        s, e = make_window(ny_anchor, sh, eh)
        windows[name] = (s, e)
    return windows


async def startup():
    global _client
    _client = httpx.AsyncClient(headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    })
    # Start microstructure engine loop
    if _micro_enabled and _micro_engine is not None:
        asyncio.create_task(_pro_signal_loop())
        logging.info("Microstructure engine started")


async def shutdown():
    await _client.aclose()


# ----- Institutional-lite Pro Signal Loop -----
async def _pro_signal_loop():
    """
    Background loop feeding microstructure engine at ~1 Hz.
    Generates institutional-lite signals based on OFI + microprice + regime.
    """
    global _last_pro_signal
    context_key = ("pro_signal_context", "XAUUSD")
    while True:
        try:
            if not _micro_enabled or _micro_engine is None:
                await asyncio.sleep(10)
                continue
            
            # Get current bid/ask from primary sources (prefer TD; use GoldAPI sparingly)
            bid, ask = None, None
            try:
                # Prefer TwelveData (long TTL to conserve credits)
                tq = await cached_twelvedata_quote("XAUUSD", ttl_sec=900)  # 15 minutes
                bid = tq.get("bid")
                ask = tq.get("ask")
                if bid is None and tq.get("last") is not None:
                    mid = float(tq.get("last"))
                    bid = mid - 0.10
                    ask = mid + 0.10
            except Exception:
                pass

            # Only hit GoldAPI if we still don't have a valid quote, and with long TTL
            if (bid is None or ask is None):
                try:
                    gq = await cached_goldapi_quote("XAUUSD", ttl_sec=1800)  # 30 minutes
                    bid = bid or gq.get("bid")
                    ask = ask or gq.get("ask")
                    if (bid is None or ask is None) and gq.get("last") is not None:
                        mid = float(gq.get("last"))
                        spread = max(0.05, 0.0005 * mid)
                        bid = mid - spread / 2.0
                        ask = mid + spread / 2.0
                except Exception:
                    pass
            
            # Feed engine if valid tick
            if bid and ask and ask > bid:
                _micro_engine.on_tick(bid, ask, time.time())
                
                context = _cache_get(context_key, ttl_ms=3_600_000)  # 60 minutes
                if not context:
                    # Get levels context for sweep detection
                    pdh, pdl = None, None
                    try:
                        lvls = await levels_today_cached("XAUUSD")
                        if lvls:
                            pdh = lvls.get("PDH")
                            pdl = lvls.get("PDL")
                    except Exception:
                        pass
                    
                    # Get ATR for stop sizing (from existing home logic or fallback)
                    atr_5m = None
                    try:
                        candles, _ = await get_candles("XAUUSD")
                        atr_5m = _wilder_atr20_from_ohlc1m(candles)
                        if atr_5m:
                            atr_5m = atr_5m / 24.0  # Scale down to ~5m equivalent
                    except Exception:
                        pass
                    context = {"pdh": pdh, "pdl": pdl, "atr_5m": atr_5m}
                    _cache_put(context_key, context)
                
                # Generate signal
                sig = _micro_engine.make_signal(pdh=context.get("pdh"), pdl=context.get("pdl"), atr_5m=context.get("atr_5m"))
                if sig:
                    sig["symbol"] = "XAUUSD"
                    sig["ts"] = now_utc_ms()
                    sig["id"] = f"pro-{sig['ts']}-{sig['side'].lower()}"
                    _last_pro_signal = sig
                    logging.info(f"Pro signal generated: {sig['side']} @ {sig['entry']} ({sig['reason']})")
                    
                    # Broadcast to websocket clients
                    await _broadcast_signal(sig)
            
            # Slowed down loop
            await asyncio.sleep(10.0)
            
        except Exception as e:
            logging.exception(f"Pro signal loop error: {e}")
            await asyncio.sleep(10.0)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def root():
    return {
        "ok": True,
        "service": "sniperflow-api",
        "endpoints": [
            "/home",
            "/v1/health",
            "/v1/levels/today?symbol=XAUUSD",
        ],
    }


@router.get("/levels/intraday")
async def intraday(symbol: str = "XAUUSD"):
    try:
        candles, last_price = await get_candles(symbol)
        # If we used TwelveData, refine with bid/ask mid to align with broker quotes
        try:
            q = await fetch_twelvedata_quote(symbol)
            bid, ask = q.get("bid"), q.get("ask")
            if bid and ask:
                last_price = (bid + ask) / 2.0
            elif q.get("last"):
                last_price = q["last"]
        except Exception:
            pass
        nyt = ny_now()
        anchor = session_day_anchor(nyt)
        cur_start = anchor
        cur_end = anchor + timedelta(days=1)
        prev_start = anchor - timedelta(days=1)
        prev_end = anchor

        cur_c = filter_candles(candles, to_utc_ms(cur_start), to_utc_ms(cur_end))
        prev_c = filter_candles(candles, to_utc_ms(prev_start), to_utc_ms(prev_end))

        cur_levels = compute_levels_for_window(cur_c)
        prev_levels = compute_levels_for_window(prev_c)
        return {
            "asOf": now_utc_ms(),
            "lastPrice": last_price,
            "DO": cur_levels["DO"],
            "PDH": prev_levels["PDH"],
            "PDL": prev_levels["PDL"],
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/levels/intraday/sessions")
async def intraday_sessions(symbol: str = "XAUUSD"):
    try:
        candles, last_price = await get_candles(symbol)
        nyt = ny_now()
        anchor = session_day_anchor(nyt)
        windows = build_sessions_windows(anchor)

        daily_cur = filter_candles(candles, to_utc_ms(anchor), to_utc_ms(anchor + timedelta(days=1)))
        daily_prev = filter_candles(candles, to_utc_ms(anchor - timedelta(days=1)), to_utc_ms(anchor))
        daily = {
            "DO": compute_levels_for_window(daily_cur)["DO"],
            "PDH": compute_levels_for_window(daily_prev)["PDH"],
            "PDL": compute_levels_for_window(daily_prev)["PDL"],
        }

        sessions = {}
        for name, (s, e) in windows.items():
            c = filter_candles(candles, to_utc_ms(s), to_utc_ms(e))
            sessions[name] = compute_levels_for_window(c)

        return {
            "asOf": now_utc_ms(),
            "lastPrice": last_price,
            "daily": daily,
            "sessions": sessions,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/market/ohlc24h")
async def ohlc_24h(symbol: str = "XAUUSD"):
    try:
        candles, last_price = await get_candles(symbol)
        end_ms = now_utc_ms()
        start_ms = end_ms - 24 * 60 * 60 * 1000
        w = filter_candles(candles, start_ms, end_ms)
        if not w:
            raise RuntimeError("No candles in 24h window")
        highs = [c["h"] for c in w]
        lows = [c["l"] for c in w]
        closes = [c["c"] for c in w]
        high24 = max(highs)
        low24 = min(lows)
        first_close = closes[0]
        change = last_price - first_close
        pct = (change / first_close) * 100.0 if first_close else 0.0
        return {
            "asOf": end_ms,
            "last": last_price,
            "high24h": high24,
            "low24h": low24,
            "change24h": change,
            "pct24h": pct,
            "closes": closes,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/calendar/upcoming")
async def calendar_upcoming(ccy: str = "USD", hours: int = 72):
    # Prefer DB-backed official sources when available
    try:
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            hrs = int(hours)
            with psycopg2.connect(db_url) as c, c.cursor() as cur:
                cur.execute(
                    """
                    SELECT title, EXTRACT(EPOCH FROM time)::bigint AS ts_sec, COALESCE(impact,'High')
                    FROM calendar
                    WHERE time BETWEEN now() AND now() + interval %s
                    ORDER BY (COALESCE(importance,0) DESC), time ASC
                    LIMIT 1
                    """,
                    (f"{hrs} hour",)
                )
                row = cur.fetchone()
                if row:
                    title, ts_sec, impact = row
                    return {
                        "next_red": {
                            "title": str(title or ""),
                            "impact": str(impact or "High"),
                            "time_utc": str(int(ts_sec)),
                            "lock_window": {
                                "start_utc": str(int(ts_sec - 900)),
                                "end_utc": str(int(ts_sec + 900)),
                            }
                        }
                    }
    except Exception:
        # fall back to empty response
        pass
    # Return null when DB not configured or no upcoming events found
    return {"next_red": None}


# ---------------- Consolidated Home Endpoint ----------------

def _z_from_tail(values, lookback: int = 120) -> float:
    v = values[-lookback:] if len(values) >= lookback else values
    if len(v) < 10:
        return 0.0
    mean = sum(v) / len(v)
    var = sum((x - mean) ** 2 for x in v) / max(1, len(v) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    z = (v[-1] - mean) / sd
    return max(-3.0, min(3.0, z))


def _find_sast_midnight_open(candles) -> Optional[float]:
    tz = pytz.timezone("Africa/Johannesburg")
    # search for first bar with 00:00 local time today
    now_local = datetime.now(tz)
    ymd = now_local.strftime("%Y-%m-%d")
    for c in candles:
        t_local = datetime.fromtimestamp(c["t"] / 1000, tz=pytz.UTC).astimezone(tz)
        if t_local.strftime("%Y-%m-%d") == ymd and t_local.hour == 0 and t_local.minute == 0:
            return c["o"]
    # fallback to first candle of local day
    for c in candles:
        t_local = datetime.fromtimestamp(c["t"] / 1000, tz=pytz.UTC).astimezone(tz)
        if t_local.strftime("%Y-%m-%d") == ymd:
            return c["o"]
    return None


async def _fetch_intraday_yf_series(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch 5m intraday candles for a Yahoo symbol."""
    if _is_yf_blocked():
        return None
    res = await yahoo_series_5m(symbol)
    if not res:
        return None
    candles, last = res
    return {"candles": candles, "last": last}


async def _fetch_intraday_yf_series_multi(symbols):
    """Try a list of Yahoo symbols in order; return first successful series."""
    for sym in symbols:
        try:
            data = await _fetch_intraday_yf_series(sym)
            if data and data.get("candles"):
                return {"symbol": sym, **data}
        except Exception:
            pass
    return None


def _resample_candles(candles, tf: str):
    """Aggregate 1m-like candles to 5m or 1h. Returns list of {t,o,h,l,c}."""
    tf_l = (tf or "").lower()
    if tf_l in ("1m", "1min", "1"):
        return candles
    if tf_l in ("5m", "5min", "5"):
        bucket_ms = 5 * 60 * 1000
    elif tf_l in ("1h", "60m", "60"):
        bucket_ms = 60 * 60 * 1000
    else:
        return candles
    buckets: Dict[int, Dict[str, float]] = {}
    for c in candles:
        t = c.get("t")
        if t is None:
            continue
        b = (int(t) // bucket_ms) * bucket_ms
        bkt = buckets.get(b)
        o = float(c.get("o")) if c.get("o") is not None else None
        h = float(c.get("h")) if c.get("h") is not None else None
        l = float(c.get("l")) if c.get("l") is not None else None
        v = float(c.get("c")) if c.get("c") is not None else None
        if o is None or h is None or l is None or v is None:
            continue
        if not bkt:
            buckets[b] = {"o": o, "h": h, "l": l, "c": v}
        else:
            bkt["h"] = max(bkt["h"], h)
            bkt["l"] = min(bkt["l"], l)
            bkt["c"] = v
    out = [{"t": k, **v} for k, v in buckets.items()]
    out.sort(key=lambda x: x["t"])
    return out


def _parse_fred_date_to_ms(date_str: str) -> int:
    naive = datetime.strptime(date_str, "%Y-%m-%d")
    aware = naive.replace(tzinfo=timezone.utc)
    return to_utc_ms(aware)


async def fetch_fred_series(series_id: str, max_points: int = 365) -> Optional[Dict[str, Any]]:
    """Fetch daily FRED series observations with simple caching; returns dict with 'ts' and 'values'."""
    key = ("fred", series_id)
    ts_payload = _cache.get(key)
    if ts_payload and (now_utc_ms() - ts_payload[0] < 12 * 60 * 60 * 1000):  # 12h cache
        return ts_payload[1]
    if not FRED_KEY:
        return None
    params = {
        "series_id": series_id,
        "api_key": FRED_KEY,
        "file_type": "json",
        "observation_start": (datetime.now(timezone.utc) - timedelta(days=max(370, max_points + 10))).strftime("%Y-%m-%d"),
    }
    r = await _client.get(FRED_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    obs = j.get("observations", [])
    values = []
    tss = []
    for o in obs:
        v = o.get("value")
        d = o.get("date")
        try:
            fv = float(v)
        except Exception:
            continue
        ts = _parse_fred_date_to_ms(d)
        values.append(fv)
        tss.append(ts)
    if not values:
        return None
    values = values[-max_points:]
    tss = tss[-max_points:]
    payload = {"values": values, "ts": tss[-1]}
    _cache[key] = (now_utc_ms(), payload)
    return payload


async def fred_latest(series_id: str) -> Optional[Dict[str, Any]]:
    """Return latest value and ts for a FRED series (cached via fetch_fred_series)."""
    try:
        s = await fetch_fred_series(series_id, max_points=365)
        if not s or not s.get("values"):
            return None
        val = s["values"][-1]
        return {"id": series_id, "value": float(val), "ts": s.get("ts")}
    except Exception:
        return None

async def _yf_chart_with_volume(symbol: str, interval: str, range_: str) -> Optional[Dict[str, Any]]:
    # Removed Yahoo volume path; return None to disable dependent metrics
        return None


async def _volume_percentile_gcf() -> Optional[int]:
    """Compute intraday cumulative volume percentile for GC=F vs last ~20 days at same 5m index."""
    # Fetch 1 month of 5m bars with volume
    data = await _yf_chart_with_volume("GC=F", interval="5m", range_="1mo")
    if not data or not data.get("candles"):
        return None
    candles = data["candles"]
    # Group by UTC day
    days: Dict[str, list] = {}
    for c in candles:
        dt = datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc)
        key = dt.strftime("%Y-%m-%d")
        days.setdefault(key, []).append(c)
    # Ensure order per day
    for k in list(days.keys()):
        days[k].sort(key=lambda x: x["t"])
    if not days:
        return None
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today_key not in days:
        # Use the most recent day as "today"
        today_key = sorted(days.keys())[-1]
    today = [c for c in days[today_key] if c.get("v") is not None]
    if not today:
        return None
    # Index by number of bars since midnight (5m cadence)
    N = len(today)
    today_cum = 0.0
    for c in today:
        today_cum += float(c.get("v", 0.0))
    # Build historical samples at same index N
    samples = []
    for k, seq in days.items():
        if k == today_key:
            continue
        seq_v = [c for c in seq if c.get("v") is not None]
        if len(seq_v) >= N:
            s = sum(float(x.get("v", 0.0)) for x in seq_v[:N])
            samples.append(s)
    if not samples:
        return None
    below_eq = len([x for x in samples if x <= today_cum])
    pct = int((below_eq / len(samples)) * 100.0)
    return max(0, min(100, pct))


def _wilder_atr20_from_ohlc1m(candles) -> Optional[float]:
    # Build daily bars from 1m candles for approx ATR20
    if not candles:
        return None
    # group by UTC date
    days: Dict[str, Dict[str, float]] = {}
    last_close = None
    for c in candles:
        if any(c.get(k) is None for k in ("o", "h", "l", "c", "t")):
            continue
        dt = datetime.fromtimestamp(c["t"]/1000, tz=timezone.utc)
        key = dt.strftime("%Y-%m-%d")
        d = days.get(key)
        o = float(c["o"]); h = float(c["h"]); l = float(c["l"]); cl = float(c["c"])
        if not d:
            days[key] = {"o": o, "h": h, "l": l, "c": cl}
        else:
            d["h"] = max(d["h"], h)
            d["l"] = min(d["l"], l)
            d["c"] = cl
    daily = []
    for k in sorted(days.keys()):
        d = days[k]
        daily.append({"o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"]})
    if len(daily) < 21:
        return None
    # true range and Wilder ATR
    def tr(prev, cur):
        return max(cur["h"] - cur["l"], max(abs(cur["h"] - prev["c"]), abs(cur["l"] - prev["c"])))
    atr = sum(tr(daily[i-1], daily[i]) for i in range(1, 21)) / 20.0
    for i in range(21, len(daily)):
        atr = (atr * 19.0 + tr(daily[i-1], daily[i])) / 20.0
    return atr


def _stub_alerts(now_ms: int):
    return [
        {
            "id": f"a-{int(now_ms/1000)-600}",
            "title": "PDH sweep + MSS",
            "age_sec": 600,
            "conf": 0.72,
            "ev_r": 1.35,
            "severity": "actionable",
        },
        {
            "id": f"a-{int(now_ms/1000)-1800}",
            "title": "Asia range expansion",
            "age_sec": 1800,
            "conf": 0.58,
            "ev_r": 0.90,
            "severity": "setup",
        },
        {
            "id": f"a-{int(now_ms/1000)-2400}",
            "title": "Liquidity pocket tagged",
            "age_sec": 2400,
            "conf": 0.41,
            "ev_r": 0.65,
            "severity": "info",
        },
    ]


@router.get("/v1/alerts")
async def v1_alerts(since: Optional[int] = None):
    """Return recent alerts (stub). since is epoch seconds; filters by age."""
    try:
        now_ms = now_utc_ms()
        alerts = _stub_alerts(now_ms)
        if since is not None:
            cutoff = since
            alerts = [a for a in alerts if int(a.get("id", "a-0").split("-")[-1]) >= cutoff]
        return alerts
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"alerts: {e}")

# ------- ML feature builder (uses the same values /home already computes) -------

def _from_drivers(drivers: list[dict], key: str, default: float = 0.0) -> float:
    for d in drivers:
        if d.get("key") == key:
            try:
                return float(d.get("value", default) or default)
            except Exception:
                return default
    return default


def _driver_freshness_map(drivers: list[dict]) -> dict[str, float]:
    m: dict[str, float] = {}
    for d in drivers:
        k = d.get("key")
        st = d.get("stale", True)
        # encode staleness as 0/1 freshness feature
        if k:
            m[f"{k}_fresh"] = 0.0 if st else 1.0
    return m


def _one_hot_session(sess: str | None) -> dict[str, float]:
    s = (sess or "").lower()
    return {
        "sess_asia":    1.0 if s == "asia"    else 0.0,
        "sess_london":  1.0 if s == "london"  else 0.0,
        "sess_newyork": 1.0 if s == "newyork" else 0.0,
        "sess_off":     1.0 if s in ("", "none", "null") or s not in ("asia","london","newyork") else 0.0,
    }


def _quality_bucket(state: str | None) -> dict[str, float]:
    s = (state or "").upper()
    return {
        "q_ok":        1.0 if s == "OK"        else 0.0,
        "q_degraded":  1.0 if s == "DEGRADED"  else 0.0,
        "q_poor":      1.0 if s == "POOR"      else 0.0,
    }


def _safe_float(x, d=None):
    try:
        return float(x) if x is not None else (0.0 if d is None else d)
    except Exception:
        return 0.0 if d is None else d


def _build_ml_features_from_home_payload(home_payload: dict) -> dict:
    """
    Pulls everything we need from the SAME structures /home assembled.
    """
    price     = home_payload.get("price", {})
    metrics   = home_payload.get("metrics", {})
    quality   = home_payload.get("quality", {})
    sessions  = home_payload.get("sessions", {})
    gates     = home_payload.get("gates", {})
    nowcast_m = metrics.get("nowcast", {}) or {}
    drivers   = nowcast_m.get("drivers", []) or []

    # Core drivers (already sign-aligned in /home)
    dxy_z   = _from_drivers(drivers, "dxyZ", 0.0)
    real_z  = _from_drivers(drivers, "realZ", 0.0)
    vix_z   = _from_drivers(drivers, "vixZ", 0.0)
    risk_z  = _from_drivers(drivers, "risk_on", 0.0)
    nom_z   = _from_drivers(drivers, "nominalZ", 0.0)
    do_ctx  = _from_drivers(drivers, "do_ctx", 0.0)
    mom     = _from_drivers(drivers, "mom", 0.0)

    # Intraday structure & hygiene
    feat = {
        "dxy_z":     _safe_float(dxy_z),
        "real_z":    _safe_float(real_z),
        "vix_z":     _safe_float(vix_z),
        "risk_z":    _safe_float(risk_z),
        "nom_z":     _safe_float(nom_z),
        "do_ctx":    _safe_float(do_ctx),
        "mom":       _safe_float(mom),
        "range_to_atr20": _safe_float(metrics.get("range_to_atr20")),
        "activity":       _safe_float(metrics.get("activity_index")),
        "vol_pct":        _safe_float(metrics.get("volume_percentile")),
        "spread_pts":     _safe_float(quality.get("spread_pts")),
        "news_lock":      1.0 if bool(gates.get("news_lock")) else 0.0,
        "gap_pct":        _safe_float(metrics.get("gap_pct") or 0.0),
        # (optional) 24h change if present
        "pct24h":         _safe_float(price.get("pct24h")),
    }

    # freshness one-hots for chips
    feat.update(_driver_freshness_map(drivers))
    # session one-hots
    feat.update(_one_hot_session(sessions.get("current")))
    # quality buckets
    feat.update(_quality_bucket(quality.get("state")))

    return feat

@router.get("/home")
async def home(nocache: bool = False):
    # Short-ttl cache to save upstream quotas (align with client cache header)
    key = ("home", "XAUUSD")
    if not nocache:
        hit = _cache_get(key, ttl_ms=5 * 1000)
        if hit is not None:
            return hit

    now_ms = now_utc_ms()
    _home_partial = {"ts": now_ms}
    provider_status: Dict[str, Any] = {"candles": None, "td_pct:DXY": None, "td_pct:VIX": None, "td_pct:SPY": None, "fred:DFII10": None, "td_quote:XAUUSD": None}
    try:
        # XAU intraday (with multi-provider fallback via get_candles)
        try:
            candles, last_price = await get_candles("XAUUSD")
            provider_status["candles"] = True
        except Exception:
            logging.exception("home: get_candles failed")
            provider_status["candles"] = False
            candles, last_price = [], None

        # Ensure we have a last price even when series are down
        if last_price is None:
            try:
                last_alpha = await fetch_alpha_fx_last("XAUUSD")
                if last_alpha is not None:
                    last_price = last_alpha
                    provider_status["alpha:last:XAUUSD"] = True
                else:
                    provider_status["alpha:last:XAUUSD"] = False
            except Exception:
                provider_status["alpha:last:XAUUSD"] = False

        # Build arrays
        ts = [c["t"] for c in candles]
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        cvals = [c["c"] for c in candles]

        # Current day (SAST) window stats
        end_ms = now_utc_ms()
        tz_sast = pytz.timezone("Africa/Johannesburg")
        now_local = datetime.now(tz_sast)
        midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_ms = int(midnight_local.astimezone(timezone.utc).timestamp() * 1000)
        day_idx = [i for i, t in enumerate(ts) if t >= midnight_ms]
        if day_idx:
            d0 = day_idx[0]
        else:
            d0 = max(0, len(ts) - 1)
        high_day = max(h[d0:]) if d0 < len(h) else None
        low_day = min(l[d0:]) if d0 < len(l) else None
        base_do = o[d0] if d0 < len(o) else None
        change_day = (last_price - base_do) if (base_do is not None) else None
        pct_day = ((change_day / base_do) * 100.0) if (base_do and base_do != 0) else None

        # SAST DO: use daily-cached DO first (stable per day), fallback to candle-derived
        do_price = None
        try:
            lv = await levels_today_cached("XAUUSD", candles=candles)
            do_price = (lv or {}).get("DO")
        except Exception:
            do_price = None
        if do_price is None:
            do_price = _find_sast_midnight_open(candles)

        # Drivers via Twelve Data percent change (cached) and FRED daily
        drivers = []
        try:
            dxy_added = False
            dxy_pct = await td_quote_pct("DXY", ttl_sec=60)
            if dxy_pct is not None:
                drivers.append({"key": "dxyZ", "value": float(dxy_pct), "stale": False})
                dxy_added = True
            provider_status["td_pct:DXY"] = dxy_added
            if not dxy_added:
                # Yahoo then FRED fallback
                try:
                    dxy_series = await _fetch_intraday_yf_series("^DXY")
                    if dxy_series and dxy_series.get("candles"):
                        c = dxy_series["candles"]
                        z = _z_from_tail([x["c"] for x in c])
                        drivers.append({"key": "dxyZ", "value": float(-z), "stale": False})
                        provider_status["yahoo:series:DXY"] = True
                        dxy_added = True
                    else:
                        provider_status["yahoo:series:DXY"] = False
                except Exception:
                    provider_status["yahoo:series:DXY"] = False
                if not dxy_added:
                    # FRED DEXUSEU or DTWEXBGS percent (as coarse fallback)
                    fp = await fetch_fred_pct("DTWEXBGS")
                    if fp is not None:
                        drivers.append({"key": "dxyZ", "value": float(fp), "stale": False})
        except Exception:
            logging.exception("home: dxy pct failed")
            provider_status["td_pct:DXY"] = False
            # Yahoo fallback for DXY pct
            try:
                dxy_series = await _fetch_intraday_yf_series("^DXY")
                if dxy_series and dxy_series.get("candles"):
                    c = dxy_series["candles"]
                    z = _z_from_tail([x["c"] for x in c])
                    drivers.append({"key": "dxyZ", "value": float(-z), "stale": False})
                    provider_status["yahoo:series:DXY"] = True
                else:
                    provider_status["yahoo:series:DXY"] = False
            except Exception:
                provider_status["yahoo:series:DXY"] = False
        try:
            vix_added = False
            vix_pct = await td_quote_pct("VIX", ttl_sec=60)
            if vix_pct is not None:
                drivers.append({"key": "vixZ", "value": float(vix_pct), "stale": False})
                vix_added = True
            provider_status["td_pct:VIX"] = vix_added
            if not vix_added:
                try:
                    vix_series = await _fetch_intraday_yf_series("^VIX")
                    if vix_series and vix_series.get("candles"):
                        c = vix_series["candles"]
                        z = _z_from_tail([x["c"] for x in c])
                        drivers.append({"key": "vixZ", "value": float(z), "stale": False})
                        provider_status["yahoo:series:VIX"] = True
                        vix_added = True
                    else:
                        provider_status["yahoo:series:VIX"] = False
                except Exception:
                    provider_status["yahoo:series:VIX"] = False
                if not vix_added:
                    try:
                        ap = await fetch_alpha_global_quote_pct("VIX")
                        if ap is not None:
                            drivers.append({"key": "vixZ", "value": float(ap), "stale": False})
                            provider_status["alpha:pct:VIX"] = True
                            vix_added = True
                        else:
                            provider_status["alpha:pct:VIX"] = False
                    except Exception:
                        provider_status["alpha:pct:VIX"] = False
        except Exception:
            logging.exception("home: vix pct failed")
            provider_status["td_pct:VIX"] = False
            try:
                vix_series = await _fetch_intraday_yf_series("^VIX")
                if vix_series and vix_series.get("candles"):
                    c = vix_series["candles"]
                    z = _z_from_tail([x["c"] for x in c])
                    drivers.append({"key": "vixZ", "value": float(z), "stale": False})
                    provider_status["yahoo:series:VIX"] = True
                else:
                    provider_status["yahoo:series:VIX"] = False
            except Exception:
                provider_status["yahoo:series:VIX"] = False
            if not vix_added:
                try:
                    ap = await fetch_alpha_global_quote_pct("VIX")
                    if ap is not None:
                        drivers.append({"key": "vixZ", "value": float(ap), "stale": False})
                        provider_status["alpha:pct:VIX"] = True
                    else:
                        provider_status["alpha:pct:VIX"] = False
                except Exception:
                    provider_status["alpha:pct:VIX"] = False
        try:
            spy_added = False
            spy_pct = await td_quote_pct("SPY", ttl_sec=60)
            if spy_pct is not None:
                drivers.append({"key": "risk_on", "value": float(spy_pct), "stale": False})
                spy_added = True
            provider_status["td_pct:SPY"] = spy_added
            if not spy_added:
                tried_alpha = False
                try:
                    spy_series = await _fetch_intraday_yf_series("SPY")
                    if spy_series and spy_series.get("candles"):
                        c = spy_series["candles"]
                        z = _z_from_tail([x["c"] for x in c])
                        drivers.append({"key": "risk_on", "value": float(z), "stale": False})
                        provider_status["yahoo:series:SPY"] = True
                        spy_added = True
                    else:
                        provider_status["yahoo:series:SPY"] = False
                except Exception:
                    provider_status["yahoo:series:SPY"] = False
                if not spy_added:
                    ap = await fetch_alpha_global_quote_pct("SPY")
                    if ap is not None:
                        drivers.append({"key": "risk_on", "value": float(ap), "stale": False})
                        provider_status["alpha:pct:SPY"] = True
                        spy_added = True
                    else:
                        provider_status["alpha:pct:SPY"] = False
                if not spy_added:
                    try:
                        fp = await fetch_fred_pct("BAMLH0A0HYM2")
                        if fp is not None:
                            drivers.append({"key": "risk_on", "value": float(-fp), "stale": True}) # FRED is daily, so mark as stale
                            provider_status["fred:pct:BAMLH0A0HYM2"] = True
                            spy_added = True
                        else:
                            provider_status["fred:pct:BAMLH0A0HYM2"] = False
                    except Exception:
                        provider_status["fred:pct:BAMLH0A0HYM2"] = False
        except Exception:
            logging.exception("home: spy pct failed")
            provider_status["td_pct:SPY"] = False
            try:
                spy_series = await _fetch_intraday_yf_series("SPY")
                if spy_series and spy_series.get("candles"):
                    c = spy_series["candles"]
                    z = _z_from_tail([x["c"] for x in c])
                    drivers.append({"key": "risk_on", "value": float(z), "stale": False})
                    provider_status["yahoo:series:SPY"] = True
                else:
                    provider_status["yahoo:series:SPY"] = False
            except Exception:
                provider_status["yahoo:series:SPY"] = False
            if not spy_added:
                try:
                    ap = await fetch_alpha_global_quote_pct("SPY")
                    if ap is not None:
                        drivers.append({"key": "risk_on", "value": float(ap), "stale": False})
                        provider_status["alpha:pct:SPY"] = True
                        spy_added = True
                    else:
                        provider_status["alpha:pct:SPY"] = False
                except Exception:
                    provider_status["alpha:pct:SPY"] = False
            if not spy_added:
                try:
                    fp = await fetch_fred_pct("BAMLH0A0HYM2")
                    if fp is not None:
                        drivers.append({"key": "risk_on", "value": float(-fp), "stale": True})
                        provider_status["fred:pct:BAMLH0A0HYM2"] = True
                    else:
                        provider_status["fred:pct:BAMLH0A0HYM2"] = False
                except Exception:
                    provider_status["fred:pct:BAMLH0A0HYM2"] = False
        try:
            real_yield_values = None
            fred = await fetch_fred_series("DFII10", max_points=365)
            if fred and fred.get("values"):
                real_yield_values = fred["values"]
                provider_status["fred:DFII10"] = True
            else:
                # Fallback: calculate from nominal and inflation expectation
                try:
                    nominal_series = await fetch_fred_series("DGS10", max_points=365)
                    inflation_series = await fetch_fred_series("T10YIE", max_points=365)
                    if nominal_series and inflation_series and nominal_series.get("values") and inflation_series.get("values"):
                        # Align and subtract; simple approach assumes lists are roughly aligned
                        nom = nominal_series["values"]
                        inf = inflation_series["values"]
                        min_len = min(len(nom), len(inf))
                        real_yield_values = [(n - i) for n, i in zip(nom[-min_len:], inf[-min_len:])]
                        provider_status["fred:DGS10-T10YIE"] = True
                except Exception:
                    provider_status["fred:DGS10-T10YIE"] = False

            if real_yield_values:
                # real yields (invert sign for gold tilt)
                drivers.append({"key": "realZ", "value": float(-_z_from_tail(real_yield_values, lookback=252)), "stale": False})
            else:
                 provider_status["fred:DFII10"] = False
        except Exception:
            logging.exception("home: real yields failed")
            provider_status["fred:DFII10"] = False
        # Add nominal 10y as an extra driver (invert sign for gold tilt)
        try:
            nominal_yield_values = None
            fred_nominal = await fetch_fred_series("DGS10", max_points=365)
            if fred_nominal and fred_nominal.get("values"):
                nominal_yield_values = fred_nominal["values"]
                provider_status["fred:DGS10"] = True
            else:
                try:
                    tnx_series = await _fetch_intraday_yf_series("^TNX")
                    if tnx_series and tnx_series.get("candles"):
                        # TNX is yield * 10
                        nominal_yield_values = [c["c"] / 10.0 for c in tnx_series["candles"]]
                        provider_status["yahoo:series:TNX"] = True
                except Exception:
                    provider_status["yahoo:series:TNX"] = False

            if nominal_yield_values:
                drivers.append({"key": "nominalZ", "value": float(-_z_from_tail(nominal_yield_values, lookback=252)), "stale": False})
            else:
                provider_status["fred:DGS10"] = False
        except Exception:
            logging.exception("home: nominal yields failed")
            provider_status["fred:DGS10"] = False

        # Calendar next red using existing stub
        cal = await calendar_upcoming("USD", 72)

        # Intraday high/low since local SAST midnight for range_to_atr20 and other metrics
        try:
            intraday = [c for c in candles if c["t"] >= midnight_ms]
            intraday_hi = max((c["h"] for c in intraday), default=None)
            intraday_lo = min((c["l"] for c in intraday), default=None)
            intraday_range = (intraday_hi - intraday_lo) if (intraday_hi is not None and intraday_lo is not None) else None
        except Exception:
            intraday_range = None

        # Range-to-ATR20 proxy: use 1% of last price as ATR20 approximation to keep values in a familiar range
        range_to_atr20 = None
        if intraday_range is not None and last_price:
            atr20_proxy = max(1e-9, 0.01 * float(last_price))
            range_to_atr20 = float(intraday_range) / atr20_proxy

        # Activity index proxy: logistic on z-score of absolute minute returns within current SAST day
        try:
            closes_day = [c["c"] for c in intraday]
            rets = []
            for i in range(1, len(closes_day)):
                a = closes_day[i-1]
                b = closes_day[i]
                if a:
                    rets.append(abs((b - a) / a))
            raw_z = _z_from_tail(rets, lookback=240)
            activity_index = 1.0 / (1.0 + math.exp(-2.0 * raw_z))
        except Exception:
            activity_index = None

        # Volume percentile proxy: percentile of latest 5-min realized volatility vs today's distribution
        volume_percentile = None
        try:
            closes_day = [c["c"] for c in intraday]
            if len(closes_day) >= 7:
                # compute windowed RV over 5-minute rolling windows
                def _rv5(window):
                    rets = []
                    for i in range(1, len(window)):
                        a = window[i-1]
                        b = window[i]
                        if a:
                            rets.append((b - a) / a)
                    if not rets:
                        return 0.0
                    m = sum(rets) / len(rets)
                    var = sum((r - m) * (r - m) for r in rets) / max(1, len(rets) - 1)
                    return math.sqrt(var)
                rv_vals = []
                for i in range(5, len(closes_day)):
                    rv_vals.append(_rv5(closes_day[i-5:i+1]))
                if rv_vals:
                    cur = rv_vals[-1]
                    below_eq = len([x for x in rv_vals if x <= cur])
                    volume_percentile = int((below_eq / len(rv_vals)) * 100.0)
                    volume_percentile = max(0, min(100, volume_percentile))
        except Exception:
            volume_percentile = None

        # Levels: prefer day cache; if unavailable and we have candles, compute
        prev_levels = {"PDH": None, "PDL": None}
        levels_cached = False
        try:
            day_levels = await levels_today_cached("XAUUSD")
            if day_levels:
                do_price = day_levels["DO"]
                prev_levels = {"PDH": day_levels["PDH"], "PDL": day_levels["PDL"]}
                levels_cached = True
        except Exception:
            pass
        if not levels_cached:
            # Strict previous UTC trading day with weekend/holiday tolerance
            prev_levels = _compute_prev_day_levels_strict_utc(candles, max_lookback_days=3)
            if prev_levels.get("PDH") is None or prev_levels.get("PDL") is None:
                try:
                    c2, _lp2 = await fetch_stooq("XAUUSD")
                    prev_levels_s = _compute_prev_day_levels_strict_utc(c2, max_lookback_days=3)
                    if prev_levels_s.get("PDH") is not None and prev_levels_s.get("PDL") is not None:
                        prev_levels = prev_levels_s
                except Exception:
                    pass

        # Rebuild macro drivers using the unified engine so signs/scale match v1 consistently
        try:
            drv_u = await _compute_drivers_payload(candles=candles, last_price=last_price)
            dxy_z = float(drv_u.get("dxy", {}).get("z", 0.0))           # already sign-adjusted (− for gold)
            real_z = float(drv_u.get("real10y", {}).get("z", 0.0))      # already sign-adjusted (− for gold)
            vix_z = float(drv_u.get("vix", {}).get("z", 0.0))
            risk_on_z = float(drv_u.get("risk_on", {}).get("z", 0.0))
            nominal_z = float(drv_u.get("nominal10y", {}).get("z", 0.0))
            # Replace driver chips to match these z-scores
            drivers = [
                {"key": "dxyZ", "value": dxy_z, "stale": not drv_u.get("dxy", {}).get("fresh", False)},
                {"key": "realZ", "value": real_z, "stale": not drv_u.get("real10y", {}).get("fresh", False)},
                {"key": "vixZ", "value": vix_z, "stale": not drv_u.get("vix", {}).get("fresh", False)},
                {"key": "risk_on", "value": risk_on_z, "stale": not drv_u.get("risk_on", {}).get("fresh", False)},
                {"key": "nominalZ", "value": nominal_z, "stale": not drv_u.get("nominal10y", {}).get("fresh", False)},
            ]
        except Exception:
            dxy_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "dxyZ"), 0.0)
            real_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "realZ"), 0.0)
            vix_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "vixZ"), 0.0)
            risk_on_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "risk_on"), 0.0)
            nominal_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "nominalZ"), 0.0)
        # Simple nowcast based on driver z-scores (logistic transform)
        # Momentum driver based on today's move vs intraday range
        mom = 0.0
        try:
            if intraday_range and intraday_range > 0 and base_do is not None:
                mom = (float(last_price) - float(base_do)) / float(intraday_range)
                mom = max(-1.0, min(1.0, mom))
        except Exception:
            mom = 0.0
        # Signs are already adjusted in drv_u (dxy and real negative when bearish for gold)
        real_z_c = max(-1.5, min(1.5, real_z))
        dxy_z_c = dxy_z
        vix_z_c = vix_z
        term_dxy = 0.50 * dxy_z_c
        term_real = 0.20 * real_z_c
        term_vix = 0.10 * vix_z_c
        term_mom = 0.35 * mom
        # risk_on contribution (not part of logit currently)
        term_risk = 0.15 * (risk_on_z if 'risk_on_z' in locals() else next((d.get("value", 0.0) for d in drivers if d.get("key") == "risk_on"), 0.0))
        # Nominal yields contribution (not part of logit currently)
        term_nominal = 0.05 * (nominal_z if 'nominal_z' in locals() else next((d.get("value", 0.0) for d in drivers if d.get("key") == "nominalZ"), 0.0))
        # DO context contribution (signed distance normalized by prev range)
        do_contrib_val = 0.0
        try:
            prev_rng = (prev_levels["PDH"] - prev_levels["PDL"]) if (prev_levels.get("PDH") and prev_levels.get("PDL")) else None
            if prev_rng and prev_rng != 0 and do_price is not None and last_price is not None:
                do_contrib_val = ((float(last_price) - float(do_price)) / float(prev_rng))
        except Exception:
            do_contrib_val = 0.0
        term_do = 0.10 * do_contrib_val
        logit = 0.0 + term_dxy + term_real + term_vix + term_mom
        p_up = 1.0 / (1.0 + math.exp(-logit))
        direction = "bull" if p_up >= 0.5 else "bear"
        confidence = max(p_up, 1.0 - p_up)

        # Add contribution fractions for driver chips
        sum_abs = sum(abs(x) for x in (term_dxy, term_real, term_vix, term_mom, term_risk, term_do, term_nominal)) or 1.0
        for d in drivers:
            if d["key"] == "dxyZ":
                d["contribution"] = term_dxy / sum_abs
            elif d["key"] == "realZ":
                d["contribution"] = term_real / sum_abs
            elif d["key"] == "vixZ":
                d["contribution"] = term_vix / sum_abs
            elif d["key"] == "risk_on":
                d["contribution"] = term_risk / sum_abs
            elif d["key"] == "nominalZ":
                d["contribution"] = term_nominal / sum_abs
        # Add DO context as a chip
        drivers.append({"key": "do_ctx", "value": do_contrib_val, "stale": False, "contribution": term_do / sum_abs})

        # Quote for bid/ask and spread/quality (prefer TD; GoldAPI last-resort)
        bid = None
        ask = None
        spread_pts = None
        # Try TwelveData first
        try:
            q = await cached_twelvedata_quote("XAUUSD", ttl_sec=5)
            bid = q.get("bid")
            ask = q.get("ask")
            if bid and ask:
                last_price = (bid + ask) / 2.0
                spread = max(0.0, float(ask) - float(bid))
                spread_pts = int(round(spread * 100))  # ~0.01 per point
            elif q.get("last") is not None:
                last_price = q.get("last")
            provider_status["td_quote:XAUUSD"] = True
        except Exception:
            logging.exception("home: td quote failed")
            provider_status["td_quote:XAUUSD"] = False
        # GoldAPI only if still missing and enabled
        if (bid is None or ask is None) and ENABLE_GOLDAPI:
            try:
                gq = await cached_goldapi_quote("XAUUSD", ttl_sec=60 * 60 * 12)  # 12h TTL
                bid = bid or gq.get("bid")
                ask = ask or gq.get("ask")
                if (bid is None or ask is None) and gq.get("last") is not None:
                    last_price = gq.get("last")
                provider_status["gapi_quote:XAUUSD"] = True
            except Exception:
                provider_status["gapi_quote:XAUUSD"] = False

        # Quality state from spread/latency with clamping for outliers
        latency_ms = 0
        if spread_pts is not None:
            try:
                # Clamp extreme spreads: if >0.5% of last price, force into degraded band
                if last_price and abs(float(spread_pts)) >= int(round(0.005 * float(last_price) * 100)):
                    spread_pts = max(21, min(30, int(round(0.003 * float(last_price) * 100))))
            except Exception:
                pass
            # Percent-aware classification: if spread <= 0.1% of last, treat as DEGRADED (not POOR)
            pct_small = False
            try:
                if last_price and float(last_price) > 0:
                    pct_small = ( (float(spread_pts) / 100.0) / float(last_price) ) <= 0.001  # 0.1%
            except Exception:
                pct_small = False
            if spread_pts <= 20 and latency_ms <= 300:
                q_state = "OK"
            elif (spread_pts <= 30 and latency_ms <= 600) or pct_small:
                q_state = "DEGRADED"
            else:
                q_state = "POOR"
        else:
            q_state = "OK"

        # News lock from calendar
        news_lock = False
        try:
            if isinstance(cal, dict):
                nr = cal.get("next_red")
                if nr and nr.get("lock_window"):
                    start_s = int(nr["lock_window"]["start_utc"]) if isinstance(nr["lock_window"]["start_utc"], str) else nr["lock_window"]["start_utc"]
                    end_s = int(nr["lock_window"]["end_utc"]) if isinstance(nr["lock_window"]["end_utc"], str) else nr["lock_window"]["end_utc"]
                    now_s = int(end_ms // 1000)
                    news_lock = (now_s >= start_s and now_s <= end_s)
        except Exception:
            news_lock = False

        # Current session (SAST): asia 01:00–09:00, london 09:00–13:00, newyork 14:30–18:00
        try:
            tz = pytz.timezone("Africa/Johannesburg")
            now_cal = datetime.now(tz)
            m = now_cal.hour * 60 + now_cal.minute
            current_session = None
            if 60 <= m < 540:
                current_session = "asia"
            elif 540 <= m < 780:
                current_session = "london"
            elif 870 <= m < 1080:
                current_session = "newyork"
            else:
                current_session = None
        except Exception:
            current_session = None

        # Compose payload (drivers caching handled separately if needed)
        # DO priority in /home: GoldAPI open -> SAST candles -> Stooq daily open
        if do_price is None:
            try:
                gq2 = await cached_goldapi_quote("XAUUSD", ttl_sec=5)
                if gq2.get("open") is not None:
                    do_price = gq2.get("open")
            except Exception:
                pass
        if do_price is None:
            try:
                do_price = await stooq_daily_open_today("XAUUSD")
            except Exception:
                pass
        # 24h high/low from candles as fallback
        high24h_calc = None
        low24h_calc = None
        try:
            start24 = end_ms - 24 * 60 * 60 * 1000
            w24 = [c for c in candles if c["t"] >= start24]
            if w24:
                high24h_calc = max(c.get("h") for c in w24 if c.get("h") is not None)
                low24h_calc = min(c.get("l") for c in w24 if c.get("l") is not None)
        except Exception:
            pass
        # FRED extras: real (DFII10), nominal (DGS10), breakeven = nominal - real
        fred_real = None
        fred_nom = None
        fred_be = None
        try:
            fr = await fred_latest("DFII10")
            fn = await fred_latest("DGS10")
            if fr and fn:
                fred_real = fr.get("value")
                fred_nom = fn.get("value")
                fred_be = float(fred_nom) - float(fred_real)
        except Exception:
            pass

        # Compatibility aliases for provider_status keys expected by the Android app
        try:
            # td_* aliases
            provider_status["td_pct_DXY"] = bool(provider_status.get("td_pct:DXY"))
            provider_status["td_pct_VIX"] = bool(provider_status.get("td_pct:VIX"))
            provider_status["td_pct_SPY"] = bool(provider_status.get("td_pct:SPY"))
            provider_status["td_quote_XAUUSD"] = bool(provider_status.get("td_quote:XAUUSD"))
            # yahoo_* aliases
            provider_status["yahoo_series_DXY"] = bool(provider_status.get("yahoo:series:DXY"))
            provider_status["yahoo_series_VIX"] = bool(provider_status.get("yahoo:series:VIX"))
            provider_status["yahoo_last_XAUUSD"] = bool(provider_status.get("yahoo:last:XAUUSD"))
        except Exception:
            pass

        # --------- NOWCAST: prefer ML if available, else fallback to heuristic ----------
        nowcast_obj = None
        try:
            _home_partial = {
                "price": {"pct24h": pct_day},
                "metrics": {
                    "gap_pct": ((last_price - do_price) / do_price * 100.0) if (do_price and do_price != 0) else None,
                    "range_to_atr20": range_to_atr20,
                    "activity_index": activity_index,
                    "volume_percentile": volume_percentile,
                    "nowcast": {
                        "drivers": drivers + [{"key": "mom", "value": mom}],
                    },
                },
                "quality": {"state": q_state, "spread_pts": spread_pts},
                "gates": {"news_lock": news_lock},
                "sessions": {"current": current_session},
            }
            feats = _build_ml_features_from_home_payload(_home_partial)
            try:
                from .ml_engine import predict_proba, available  # type: ignore
            except Exception:
                from ml_engine import predict_proba, available  # type: ignore
            ml_prob = predict_proba(feats) if available() else None
        except Exception:
            ml_prob = None
        if ml_prob is not None:
            p = float(ml_prob)
            nowcast_obj = {
                "direction": "bull" if p >= 0.5 else "bear",
                "confidence": abs(p - 0.5) * 2.0,
                "window_min": 60,
                "drivers": drivers + [{"key": "mom", "value": mom}],
                "model_id": "ml-onnx-001",
                "updated_at": end_ms,
            }
        else:
            nowcast_obj = {
                "direction": direction,
                "confidence": confidence,
                "window_min": 60,
                "drivers": drivers + [{"key": "mom", "value": mom}],
                "model_id": "stub-000",
                "updated_at": end_ms,
            }

        payload = {
            "price": {
                "last": last_price,
                # These fields are computed for current SAST day
                "change24h": change_day,
                "pct24h": pct_day,
                "updatedAt": end_ms,
                "staleSec": 0 if last_price is not None else None,
                "closes": [c["c"] for c in intraday] if ('intraday' in locals() and intraday) else ([_ for _ in []]),
                "bid": bid,
                "ask": ask,
                # 24h high/low: prefer GoldAPI if present, else candles
                **(lambda: (lambda gq: {"high24h": gq.get("high"), "low24h": gq.get("low")}) (gq) if 'gq' in locals() and isinstance(gq, dict) else {})(),
                **({"high24h": high24h_calc} if high24h_calc is not None else {}),
                **({"low24h": low24h_calc} if low24h_calc is not None else {}),
            },
            "levels": {
                "do": {"price": do_price},
                # Previous session high/low
                "pdh": {"price": prev_levels["PDH"]},
                "pdl": {"price": prev_levels["PDL"]},
                "cached": levels_cached,
            },
            "metrics": {
                "gap_pct": ((last_price - do_price) / do_price * 100.0) if (do_price and do_price != 0) else None,
                "range_to_atr20": range_to_atr20,
                "volume_percentile": volume_percentile,
                "activity_index": activity_index,
                "nowcast": nowcast_obj,
            },
            "calendar": {"next_red": cal.get("next_red")} if isinstance(cal, dict) else {},
            "sessions": {"overlap_with_ny": (8 <= ny_now().hour < 12), "current": current_session},
            "quality": {"state": q_state, "spread_pts": spread_pts, "latency_ms": latency_ms},
            "gates": {"plan_lock": False, "reason": None, "news_lock": news_lock},
            "fred": {"real10y": fred_real, "nominal10y": fred_nom, "breakeven10y": fred_be},
            # Best-effort recent alerts preview (stub). Replace with your real alerting pipeline.
            "alerts": [
                {
                    "id": f"a-{int(end_ms/1000)-600}",
                    "title": "PDH sweep + MSS",
                    "age_sec": 600,
                    "conf": 0.72,
                    "ev_r": 1.35,
                    "severity": "actionable",
                },
                {
                    "id": f"a-{int(end_ms/1000)-1800}",
                    "title": "Asia range expansion",
                    "age_sec": 1800,
                    "conf": 0.58,
                    "ev_r": 0.90,
                    "severity": "setup",
                },
                {
                    "id": f"a-{int(end_ms/1000)-2400}",
                    "title": "Liquidity pocket tagged",
                    "age_sec": 2400,
                    "conf": 0.41,
                    "ev_r": 0.65,
                    "severity": "info",
                },
            ],
        }
        payload["provider_status"] = provider_status
        if not nocache:
            try:
                _cache_put(key, payload)
            except Exception:
                pass
        from fastapi import Response
        r = Response()
        r.headers["Cache-Control"] = "public, max-age=5"
        return payload
    except Exception as e:
        logging.exception("home: fatal error")
        # Best-effort partial structure to avoid 502s
        end_ms = now_utc_ms()
        return {
            "_warning": "partial",
            "_err": str(e),
            "price": {"last": None, "change24h": None, "pct24h": None, "high24h": None, "low24h": None, "updatedAt": end_ms, "closes": None, "bid": None, "ask": None},
            "levels": {"do": {"price": None}, "pdh": {"price": None}, "pdl": {"price": None}},
            "metrics": {"gap_pct": None, "range_to_atr20": None, "volume_percentile": None, "activity_index": None, "nowcast": {"direction": None, "confidence": None, "window_min": 60, "drivers": [], "model_id": "stub-000", "updated_at": end_ms}},
            "calendar": {},
            "sessions": {"overlap_with_ny": False, "current": None},
            "quality": {"state": "OK", "spread_pts": None, "latency_ms": 0},
            "gates": {"plan_lock": False, "reason": None, "news_lock": False},
            "alerts": [],
            "provider_status": provider_status
        }


# ---------------- v1 DRIVERS / NOWCAST / FEATURES ----------------

def _staleness(last_ts_ms: int, now_ms: int, fresh_threshold_min: int = 10) -> tuple[bool, int]:
    """Return (fresh, staleSec) given last data timestamp and 'now'."""
    if not last_ts_ms:
        return (False, 0)
    delta_s = max(0, (now_ms - last_ts_ms) // 1000)
    return (delta_s <= fresh_threshold_min * 60, delta_s)


async def _compute_drivers_payload(candles: Optional[list] = None, last_price: Optional[float] = None) -> Dict[str, Any]:
    """
    Internal helper that pulls intraday ^DXY, ^VIX, ^TNX from Yahoo,
    computes z-scores, freshness, and returns the drivers dict.
    Signs: for gold, negative DXY and negative real yields are supportive.
    """
    end_ms = now_utc_ms()
    out = {}

    # Prefer TwelveData percent changes for DXY/VIX/SPY
    td_dxy = td_vix = td_spy = None
    try:
        td_dxy, td_vix, td_spy = await asyncio.gather(
            td_quote_pct("DXY"),
            td_quote_pct("VIX"),
            td_quote_pct("SPY"),
        )
    except Exception:
        td_dxy = td_vix = td_spy = None
    if td_dxy is not None:
        out["dxy"] = {"z": float(-td_dxy), "w": 0.35, "fresh": True, "staleSec": 0}
    if td_vix is not None:
        out["vix"] = {"z": float(td_vix), "w": 0.20, "fresh": True, "staleSec": 0}
    if td_spy is not None:
        z_risk = float(td_spy)
        if td_vix is not None:
            z_risk = 0.60 * float(td_spy) - 0.40 * float(td_vix)
        out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": True, "staleSec": 0}

    # DXY fallback chain: DX-Y.NYB -> DX=F -> UUP -> ^DXY
    dxy = await _fetch_intraday_yf_series_multi(["DX-Y.NYB", "DX=F", "UUP", "^DXY"])
    vix = await _fetch_intraday_yf_series("^VIX")
    tnx = await _fetch_intraday_yf_series("^TNX")  # 10y nominal yield *10
    es = await _fetch_intraday_yf_series("ES=F")   # S&P futures for risk-on

    if "dxy" not in out and dxy and dxy.get("candles"):
        last_ts = dxy["candles"][-1]["t"]
        # Allow up to 30 minutes before calling it stale
        fresh, stale = _staleness(last_ts, end_ms, fresh_threshold_min=30)
        z = _z_from_tail([c["c"] for c in dxy["candles"]])
        out["dxy"] = {"z": float(-z), "w": 0.35, "fresh": fresh, "staleSec": stale, "sym": dxy.get("symbol")}
    elif "dxy" not in out:
        # Fallback to TwelveData percent change if available
        try:
            pct = await td_quote_pct("DXY")
            if pct is not None:
                out["dxy"] = {"z": float(-pct), "w": 0.35, "fresh": True, "staleSec": 0}
            else:
                ap = await alpha_dxy_pct()
                if ap is not None:
                    out["dxy"] = {"z": float(-ap), "w": 0.35, "fresh": True, "staleSec": 0}
                else:
                    out["dxy"] = {"z": 0.0, "w": 0.35, "fresh": False, "staleSec": None}
        except Exception:
            ap = await alpha_dxy_pct()
            if ap is not None:
                out["dxy"] = {"z": float(-ap), "w": 0.35, "fresh": True, "staleSec": 0}
            else:
                out["dxy"] = {"z": 0.0, "w": 0.35, "fresh": False, "staleSec": None}

    if "vix" not in out and vix and vix["candles"]:
        last_ts = vix["candles"][-1]["t"]
        fresh, stale = _staleness(last_ts, end_ms, fresh_threshold_min=30)
        z = _z_from_tail([c["c"] for c in vix["candles"]])
        out["vix"] = {"z": float(z), "w": 0.20, "fresh": fresh, "staleSec": stale}
    elif "vix" not in out:
        try:
            pct = await td_quote_pct("VIX")
            if pct is not None:
                out["vix"] = {"z": float(pct), "w": 0.20, "fresh": True, "staleSec": 0}
            else:
                ap = await fetch_alpha_global_quote_pct("VIX")
                if ap is not None:
                    out["vix"] = {"z": float(ap), "w": 0.20, "fresh": True, "staleSec": 0}
                else:
                    out["vix"] = {"z": 0.0, "w": 0.20, "fresh": False, "staleSec": None}
        except Exception:
            ap = await fetch_alpha_global_quote_pct("VIX")
            if ap is not None:
                out["vix"] = {"z": float(ap), "w": 0.20, "fresh": True, "staleSec": 0}
            else:
                out["vix"] = {"z": 0.0, "w": 0.20, "fresh": False, "staleSec": None}

    if tnx and tnx.get("candles"):
        last_ts = tnx["candles"][-1]["t"]
        fresh, stale = _staleness(last_ts, end_ms, fresh_threshold_min=30)
        z = _z_from_tail([c["c"]/10.0 for c in tnx["candles"]])
        out["nominal10y"] = {"z": float(-z), "w": 0.20, "fresh": fresh, "staleSec": stale}
    else:
        # Fallback to FRED DGS10 daily series
        try:
            fred_nom = await fetch_fred_series("DGS10", max_points=365)
            if fred_nom and fred_nom.get("values"):
                z = _z_from_tail(fred_nom["values"], lookback=252)
                out["nominal10y"] = {"z": float(-z), "w": 0.20, "fresh": True, "staleSec": 0}
            else:
                out["nominal10y"] = {"z": 0.0, "w": 0.20, "fresh": False, "staleSec": None}
        except Exception:
            out["nominal10y"] = {"z": 0.0, "w": 0.20, "fresh": False, "staleSec": None}

    # Real 10y from FRED DFII10 (daily)
    try:
        fred = await fetch_fred_series("DFII10", max_points=365)
        if fred and fred.get("values"):
            z = _z_from_tail(fred["values"], lookback=252)
            # invert sign for gold (higher real yield -> negative)
            out["real10y"] = {"z": float(-z), "w": 0.35, "fresh": True, "staleSec": None}
        elif "real10y" not in out:
            out["real10y"] = {"z": 0.0, "w": 0.35, "fresh": False, "staleSec": None}
    except Exception:
        if "real10y" not in out:
            out["real10y"] = {"z": 0.0, "w": 0.35, "fresh": False, "staleSec": None}

    # Risk-on proxy: +ES=F and -VIX
    if "risk_on" not in out and es and es.get("candles") and vix and vix.get("candles"):
        last_ts = min(es["candles"][-1]["t"], vix["candles"][-1]["t"])
        fresh, stale = _staleness(last_ts, end_ms, fresh_threshold_min=30)
        z_es = _z_from_tail([c["c"] for c in es["candles"]])
        z_vix = _z_from_tail([c["c"] for c in vix["candles"]])
        z_risk = 0.60 * z_es - 0.40 * z_vix
        out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": fresh, "staleSec": stale}
    elif "risk_on" not in out:
        # Fallback using TD percent changes for SPY and VIX
        try:
            spy_pct = await td_quote_pct("SPY")
            vix_pct = await td_quote_pct("VIX")
            if spy_pct is not None and vix_pct is not None:
                z_risk = 0.60 * float(spy_pct) - 0.40 * float(vix_pct)
                out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": True, "staleSec": 0}
            elif spy_pct is not None:
                out["risk_on"] = {"z": float(spy_pct), "w": 0.10, "fresh": True, "staleSec": 0}
            else:
                sp = await fetch_alpha_global_quote_pct("SPY")
                vx = await fetch_alpha_global_quote_pct("VIX")
                if sp is not None and vx is not None:
                    z_risk = 0.60 * float(sp) - 0.40 * float(vx)
                    out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": True, "staleSec": 0}
                elif sp is not None:
                    out["risk_on"] = {"z": float(sp), "w": 0.10, "fresh": True, "staleSec": 0}
                else:
                    out["risk_on"] = {"z": 0.0, "w": 0.10, "fresh": False, "staleSec": None}
        except Exception:
            sp = await fetch_alpha_global_quote_pct("SPY")
            vx = await fetch_alpha_global_quote_pct("VIX")
            if sp is not None and vx is not None:
                z_risk = 0.60 * float(sp) - 0.40 * float(vx)
                out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": True, "staleSec": 0}
            elif sp is not None:
                out["risk_on"] = {"z": float(sp), "w": 0.10, "fresh": True, "staleSec": 0}
            else:
                out["risk_on"] = {"z": 0.0, "w": 0.10, "fresh": False, "staleSec": None}

    # DO context driver (distance to DO/PDH/PDL, signed by current price vs DO)
    try:
        local_candles, local_last_price = candles, last_price
        if local_candles is None or local_last_price is None:
            local_candles, local_last_price = await get_candles("XAUUSD")
        nyt = ny_now()
        anchor = session_day_anchor(nyt)
        today = filter_candles(local_candles, to_utc_ms(anchor), to_utc_ms(anchor + timedelta(days=1)))
        prev = filter_candles(local_candles, to_utc_ms(anchor - timedelta(days=1)), to_utc_ms(anchor))
        do_price = compute_levels_for_window(today)["DO"]
        prev_levels = compute_levels_for_window(prev)
        pdh = prev_levels.get("PDH")
        pdl = prev_levels.get("PDL")
        z = 0.0
        if do_price and pdh and pdl and (pdh - pdl) != 0:
            # signed distance normalized by previous range
            z = ((float(local_last_price) - float(do_price)) / (float(pdh) - float(pdl))) * 2.0  # scale into ~[-2,2]
        out["do_ctx"] = {"z": float(max(-3.0, min(3.0, z))), "w": 0.10, "fresh": True, "staleSec": 0}
    except Exception:
        out.setdefault("do_ctx", {"z": 0.0, "w": 0.10, "fresh": False, "staleSec": None})

    return out


@router.get("/v1/drivers")
async def v1_drivers(nocache: bool = False):
    """
    Macro drivers used by the client: DXY (−), real10y (−), VIX (+).
    Returns z-scores, weights, freshness flags, and staleness seconds.
    """
    try:
        key = ("drivers", "XAUUSD")
        ts_payload = _cache.get(key)
        if not nocache and ts_payload and (now_utc_ms() - ts_payload[0] < 60_000):
            from fastapi import Response
            r = Response()
            r.headers["Cache-Control"] = "public, max-age=60"
            return ts_payload[1]
        payload = await _compute_drivers_payload()
        if not nocache:
            _cache[key] = (now_utc_ms(), payload)
        from fastapi import Response
        r = Response()
        r.headers["Cache-Control"] = "public, max-age=60"
        return payload
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"drivers: {e}")


@router.get("/v1/nowcast")
async def v1_nowcast(nocache: bool = False):
    """
    Simple nowcast score in [-100, 100], based on drivers with signs:
      logit = 0.60 * dxy.z  +  0.20 * real10y.z  +  0.20 * vix.z
      score = clip(logit, -1..1) * 100
    (dxy.z and real10y.z are already sign-adjusted inside _compute_drivers_payload)
    """
    try:
        # reuse cached drivers to reduce hits
        key = ("drivers", "XAUUSD")
        ts_payload = _cache.get(key)
        if not nocache and ts_payload and (now_utc_ms() - ts_payload[0] < 60_000):
            drv = ts_payload[1]
        else:
            drv = await _compute_drivers_payload()
            if not nocache:
                _cache[key] = (now_utc_ms(), drv)
        # Try ML first by building features from a lightweight home-like snapshot
        try:
            drivers_list = []
            for k, d in drv.items():
                if k == "dxy":
                    key_id = "dxyZ"
                elif k == "real10y":
                    key_id = "realZ"
                elif k == "vix":
                    key_id = "vixZ"
                elif k == "risk_on":
                    key_id = "risk_on"
                elif k == "nominal10y":
                    key_id = "nominalZ"
                elif k == "do_ctx":
                    key_id = "do_ctx"
                else:
                    key_id = None
                if key_id is not None:
                    drivers_list.append({
                        "key": key_id,
                        "value": d.get("z", 0.0),
                        "stale": not d.get("fresh", False)
                    })
            # add simple momentum proxy (optional)
            drivers_list.append({"key": "mom", "value": 0.0})

            snapshot = {
                "price": {"pct24h": None},
                "metrics": {
                    "gap_pct": None,
                    "range_to_atr20": None,
                    "activity_index": None,
                    "volume_percentile": None,
                    "nowcast": {"drivers": drivers_list},
                },
                "quality": {"state": "OK", "spread_pts": None},
                "gates": {"news_lock": False},
                "sessions": {"current": None},
            }
            feats = _build_ml_features_from_home_payload(snapshot)
            try:
                from .ml_engine import predict_proba, available  # type: ignore
            except Exception:
                from ml_engine import predict_proba, available  # type: ignore
            p = predict_proba(feats) if available() else None
            if p is not None:
                score = int(round(max(-1.0, min(1.0, (float(p) - 0.5) * 2.0)) * 100))
                flat = []
                for _k, _d in drv.items():
                    flat.append({
                        "id": _k,
                        "z": _d.get("z", 0.0),
                        "w": _d.get("w", 0.0),
                        "fresh": _d.get("fresh", False),
                        "staleSec": _d.get("staleSec"),
                    })
                return {"score": score, "prob_up": float(p), "drivers": flat, "ts": now_utc_ms()}
        except Exception:
            pass
        # Apply staleness decay: w' = w * exp(-staleSec / tau)
        import math as _m
        try:
            tau = int(os.getenv("NOWCAST_TAU_SEC", "2700"))
        except Exception:
            tau = 45 * 60
        def decay(d):
            w = float(d.get("w", 0.0))
            s = d.get("staleSec")
            if s is None:
                return w
            try:
                return w * _m.exp(-(float(s) / float(tau)))
            except Exception:
                return w
        # Compose drivers used by model
        parts = []
        for k in ("dxy", "real10y", "vix", "risk_on"):
            if k in drv:
                d = drv[k]
                parts.append((float(d.get("z", 0.0)), decay(d)))
        logit = sum(z * w for (z, w) in parts)
        score = int(round(max(-1.0, min(1.0, logit)) * 100.0))
        # Flatten drivers to list and include staleSec
        flat = []
        for k, d in drv.items():
            flat.append({"id": k, "z": d.get("z", 0.0), "w": d.get("w", 0.0), "fresh": d.get("fresh", False), "staleSec": d.get("staleSec")})
        return {"score": score, "drivers": flat, "ts": now_utc_ms()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"nowcast: {e}")


@router.get("/v1/features")
async def v1_features(symbol: str = "XAUUSD"):
    """
    Feature panel for the app: gap %, ATR20x proxy, activity, volume percentile,
    24h high/low, and quality from bid/ask spread when available.
    """
    try:
        candles, last_price = await get_candles(symbol)

        end_ms = now_utc_ms()
        # 24h window (internal only). Prefer GoldAPI high/low if available.
        start_ms = end_ms - 24 * 60 * 60 * 1000
        w = [c for c in candles if c["t"] >= start_ms]

        # Gap%: prefer GoldAPI open vs last close from previous UTC day
        now_utc = datetime.now(timezone.utc)
        today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_midnight = today_midnight - timedelta(days=1)
        today_w = filter_candles(candles, to_utc_ms(today_midnight), to_utc_ms(today_midnight + timedelta(days=1)))
        prev_w = filter_candles(candles, to_utc_ms(prev_midnight), to_utc_ms(today_midnight))
        today_open = today_w[0]["o"] if today_w else None
        prev_close = prev_w[-1]["c"] if prev_w else None
        try:
            gq = await cached_goldapi_quote(symbol)
            if gq.get("open") is not None:
                today_open = gq.get("open")
        except Exception:
            pass
        gap_pct = None
        if prev_close and prev_close != 0 and today_open:
            gap_pct = (today_open - prev_close) / prev_close * 100.0

        # Intraday range & ATR20 proxy (SAST day)
        tz_sast = pytz.timezone("Africa/Johannesburg")
        midnight_local = datetime.now(tz_sast).replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_ms = int(midnight_local.astimezone(timezone.utc).timestamp() * 1000)
        intraday = [c for c in candles if c["t"] >= midnight_ms]
        intraday_hi = max((c["h"] for c in intraday), default=None)
        intraday_lo = min((c["l"] for c in intraday), default=None)
        intraday_range = (intraday_hi - intraday_lo) if (intraday_hi is not None and intraday_lo is not None) else None

        # Wilder ATR20 from daily, using our 1m candles aggregation
        atr20 = _wilder_atr20_from_ohlc1m(candles)
        atr20x = None
        if intraday_range is not None and atr20 is not None and atr20 > 0:
            atr20x = float(intraday_range) / float(atr20)

        # Activity index (logistic of z on absolute 1m returns in SAST day)
        activity = None
        try:
            closes_day = [c["c"] for c in intraday]
            rets = []
            for i in range(1, len(closes_day)):
                a, b = closes_day[i-1], closes_day[i]
                if a:
                    rets.append(abs((b - a) / a))
            raw_z = _z_from_tail(rets, lookback=240)
            activity = 1.0 / (1.0 + math.exp(-2.0 * raw_z))
        except Exception:
            activity = None

        # Volume percentile proxy: percentile of latest 5m realized volatility vs today's distribution (same as /home)
        volPct = None
        try:
            closes_day = [c["c"] for c in intraday]
            if len(closes_day) >= 7:
                def _rv5(window):
                    rets = []
                    for i in range(1, len(window)):
                        a = window[i-1]
                        b = window[i]
                        if a:
                            rets.append((b - a) / a)
                    if not rets:
                        return 0.0
                    m = sum(rets) / len(rets)
                    var = sum((r - m) * (r - m) for r in rets) / max(1, len(rets) - 1)
                    return math.sqrt(var)
                rv_vals = []
                for i in range(5, len(closes_day)):
                    rv_vals.append(_rv5(closes_day[i-5:i+1]))
                if rv_vals:
                    cur = rv_vals[-1]
                    below_eq = len([x for x in rv_vals if x <= cur])
                    volPct = int((below_eq / len(rv_vals)) * 100.0)
                    volPct = max(0, min(100, volPct))
        except Exception:
            volPct = None

        # Quality from bid/ask spread if available
        quality = "OK"
        try:
            q = await cached_twelvedata_quote(symbol)
            bid, ask = q.get("bid"), q.get("ask")
            if bid and ask:
                spread_pts = int(round(max(0.0, float(ask) - float(bid)) * 100))
                if spread_pts <= 20:
                    quality = "OK"
                elif spread_pts <= 30:
                    quality = "DEGRADED"
                else:
                    quality = "POOR"
        except Exception:
            pass

        # Freshness: last bar within 5 minutes
        fresh, stale_sec = _staleness(candles[-1]["t"] if candles else 0, end_ms, fresh_threshold_min=5)

        # Add FRED block (best-effort)
        fred_block = None
        try:
            fr = await fred_latest("DFII10")
            fn = await fred_latest("DGS10")
            if fr and fn:
                fred_block = {
                    "real10y": fr.get("value"),
                    "nominal10y": fn.get("value"),
                    "breakeven10y": float(fn.get("value")) - float(fr.get("value"))
                }
        except Exception:
            fred_block = None

        # Try GoldAPI 24h high/low if present
        h24 = None; l24 = None
        try:
            gq = await cached_goldapi_quote(symbol)
            h24 = gq.get("high")
            l24 = gq.get("low")
        except Exception:
            pass
        return {
            "gapPct": gap_pct,
            "atr20x": atr20x,
            "volPct": volPct,
            "activity": activity,
            # Keep h24/l24 for features if provided by GoldAPI
            **({"h24": h24, "l24": l24} if (h24 is not None and l24 is not None) else {}),
            "quality": quality,
            "fresh": fresh,
            "staleSec": stale_sec,
            "ts": end_ms,
            "fred": fred_block,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"features: {e}")


@router.get("/v1/price/tick")
async def v1_price_tick(symbol: str = "XAUUSD", source: str | None = None):
    """
    Lightweight tick endpoint: returns bid/ask if available (TwelveData quote),
    otherwise synthesizes bid/ask around last. Includes freshness flag.
    """
    try:
        end_ms = now_utc_ms()
        bid = None
        ask = None
        last = None
        # Source override for diagnostics
        async def _use_goldapi():
            nonlocal bid, ask, last
            gq = await cached_goldapi_quote(symbol, ttl_sec=5)
            bid = gq.get("bid"); ask = gq.get("ask"); last = gq.get("last")
        async def _use_td():
            nonlocal bid, ask, last
            q = await fetch_twelvedata_quote(symbol)
            bid = q.get("bid"); ask = q.get("ask"); last = q.get("last")
        try:
            if source == "goldapi":
                await _use_goldapi()
            elif source == "twelvedata":
                await _use_td()
            else:
                # GoldAPI preferred
                await _use_goldapi()
        except Exception:
            try:
                await _use_td()
            except Exception:
                pass
        if last is None and (bid is None or ask is None):
            try:
                _candles, last_p = await get_candles(symbol)
                last = last_p
            except Exception:
                last = None
        if last is not None and (bid is None or ask is None):
            # synthesize minimal spread
            spread = max(0.05, 0.0005 * float(last))
            bid = float(last) - spread / 2.0
            ask = float(last) + spread / 2.0
        fresh = True
        try:
            candles, _lp = await get_candles(symbol)
            last_ts = candles[-1]["t"] if candles else 0
            fresh, _stale = _staleness(last_ts, end_ms, fresh_threshold_min=2)
        except Exception:
            pass
        # Quality and staleness
        quality = "stale" if not fresh else "OK"
        stale_sec = None
        try:
            candles, _lp = await get_candles(symbol)
            last_ts = candles[-1]["t"] if candles else 0
            _fresh2, stale = _staleness(last_ts, end_ms, fresh_threshold_min=2)
            stale_sec = stale
            if _fresh2 and quality == "OK" and bid is not None and ask is not None:
                spread_pts = int(round(max(0.0, float(ask) - float(bid)) * 100))
                if spread_pts <= 20:
                    quality = "ok"
                elif spread_pts <= 30:
                    quality = "degraded"
                else:
                    quality = "stale"
        except Exception:
            pass
        out = {"ts": end_ms, "fresh": fresh, "staleSec": stale_sec, "quality": quality}
        if bid is not None:
            out["bid"] = float(bid)
        if ask is not None:
            out["ask"] = float(ask)
        return out
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"price/tick: {e}")


@router.get("/v1/ohlc")
async def v1_ohlc(symbol: str = "XAUUSD", tf: str = "1m", limit: int = 1000, source: str | None = None):
    """
    Normalized OHLC fetcher. For now, uses get_candles() and slices the tail.
    tf is accepted for compatibility (1m/5m/1h), but current implementation
    returns the native interval of the provider path.
    """
    try:
        end_ms = now_utc_ms()
        # Optional source override for diagnostics
        if source in ("twelvedata", "dukascopy", "stooq", "alpha"):
            os.environ["PRICE_SOURCE"] = source
        candles, _last = await get_candles(symbol)
        if source in ("twelvedata", "dukascopy", "stooq", "alpha"):
            os.environ["PRICE_SOURCE"] = os.getenv("PRICE_SOURCE", "auto")
        # Resample if requested (server-side) to 5m or 1h
        bars = _resample_candles(candles, tf)
        if limit and limit > 0:
            bars = bars[-limit:]
        fresh, _stale = _staleness(bars[-1]["t"] if bars else 0, end_ms, fresh_threshold_min=5)
        from fastapi import Response
        r = Response()
        r.headers["Cache-Control"] = "public, max-age=30"
        return {
            "symbol": symbol,
            "tf": tf,
            "bars": bars,
            "fresh": fresh,
            "staleSec": _stale if bars else None,
            "ts": end_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ohlc: {e}")


@router.get("/v1/levels/today")
async def v1_levels_today(symbol: str = "XAUUSD"):
    """
    UTC-based levels for today: DO (open at 00:00 UTC), and PDH/PDL from
    the previous UTC day. Computed from the current candles feed.
    """
    try:
        # Preferred PDH/PDL from Stooq daily
        prev_levels = await stooq_daily_pdh_pdl(symbol)
        # DO priority: GoldAPI open -> candles -> Stooq daily open
        do_price = None
        try:
            gq = await cached_goldapi_quote(symbol)
            do_price = gq.get("open") if gq else None
        except Exception:
            pass
        if do_price is None:
            try:
                candles, _last = await get_candles(symbol)
                now_utc = datetime.now(timezone.utc)
                today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                next_midnight = today_midnight + timedelta(days=1)
                today_window = filter_candles(candles, to_utc_ms(today_midnight), to_utc_ms(next_midnight))
                do_price = compute_levels_for_window(today_window)["DO"]
            except Exception:
                pass
        if do_price is None:
            try:
                do_price = await stooq_daily_open_today(symbol)
            except Exception:
                pass
        return {"DO": do_price, "PDH": prev_levels.get("PDH"), "PDL": prev_levels.get("PDL"), "ts": now_utc_ms()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"levels/today: {e}")


@router.get("/v1/calendar/upcoming")
async def v1_calendar_upcoming(window: str = "8h", ccy: str = "USD"):
    """
    Windowed upcoming calendar wrapper. Accepts window like "8h" or "24h",
    delegates to the existing calendar stub using hours.
    """
    try:
        hours = 8
        try:
            s = window.strip().lower()
            if s.endswith("h"):
                hours = int(s[:-1])
            else:
                hours = int(s)
        except Exception:
            hours = 8
        return await calendar_upcoming(ccy=ccy, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"calendar/upcoming: {e}")


 # ---------------- WebSocket for Ticks (best-effort) ----------------
from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ticks")
async def ws_ticks(ws: WebSocket):
    await ws.accept()
    logging.info("ws_ticks: client connected")
    last_sent = 0
    while True:
        try:
            # Poll for latest price every 10s to conserve API quota
            now = now_utc_ms()
            if now > (last_sent + 10000):
                bid, ask, last = None, None, None
                # Prefer TD for ticks; avoid GoldAPI (100 req/month)
                try:
                    q = await cached_twelvedata_quote("XAUUSD", ttl_sec=5)
                    bid = q.get("bid")
                    ask = q.get("ask")
                    last = q.get("last")
                except Exception:
                    pass
                if last is not None and (bid is None or ask is None):
                    # synthesize a tiny spread if missing
                    spread = max(0.05, 0.0005 * float(last))
                    bid = float(last) - spread / 2.0
                    ask = float(last) + spread / 2.0
                await ws.send_json({"ts": now, "bid": bid, "ask": ask})
                last_sent = now
            await asyncio.sleep(1) # short sleep to yield
        except WebSocketDisconnect:
            logging.info("ws_ticks: client disconnected")
            break


@router.get("/v1/status")
async def v1_status(symbol: str = "XAUUSD"):
    """
    Compact status snapshot for diagnostics: freshness of candles, last price,
    bid/ask spread (pts), quality, provider flags, and timestamps.
    """
    try:
        end_ms = now_utc_ms()
        candles, last_price = await get_candles(symbol)
        last_ts = candles[-1]["t"] if candles else 0
        # reuse quality logic via a lightweight quote fetch
        bid = None; ask = None; spread_pts = None
        try:
            q = await cached_twelvedata_quote(symbol, ttl_sec=300)
            bid = q.get("bid"); ask = q.get("ask")
            if bid and ask:
                spread_pts = int(round(max(0.0, float(ask) - float(bid)) * 100))
        except Exception:
            pass
        fresh, stale_sec = (False, None)
        if last_ts:
            fresh = (end_ms - last_ts) <= 7 * 60 * 1000
            stale_sec = max(0, (end_ms - last_ts) // 1000)
        quality = "OK"
        if spread_pts is not None:
            # same clamp thresholds as /home
            if last_price and abs(float(spread_pts)) >= int(round(0.005 * float(last_price) * 100)):
                spread_pts = max(21, min(30, int(round(0.003 * float(last_price) * 100))))
            quality = "OK" if spread_pts <= 20 else ("DEGRADED" if spread_pts <= 30 else "POOR")
        return {
            "symbol": symbol,
            "fresh": fresh,
            "staleSec": stale_sec,
            "last": last_price,
            "spreadPts": spread_pts,
            "quality": quality,
            "ts": end_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"status: {e}")


@router.get("/v1/news")
async def v1_news(symbols: str | None = None, q: str | None = None, limit: int = 20):
    """
    Aggregate news from multiple free sources (cached 5 minutes):
      - Yahoo Finance RSS (symbols)
      - Google News RSS (query)
      - GDELT Doc API (query)

    Returns: { items: [ { title, link, ts, src } ] }
    Params:
      symbols: comma-separated Yahoo symbols (e.g., GC=F,^DXY,SPY)
      q: search query for Google News / GDELT (e.g., "gold OR XAUUSD OR DXY OR VIX")
      limit: max items (1..100)
    """
    try:
        cache_key = ("news", symbols or "-", q or "-")
        hit = _cache_get(cache_key, ttl_ms=5 * 60 * 1000)
        if hit is not None:
            return hit

        # Defaults
        syms: list[str] = []
        if symbols and symbols.strip():
            syms = [s.strip() for s in symbols.split(",") if s.strip()]
        if not syms:
            syms = ["GC=F", "XAUUSD=X", "DX-Y.NYB", "^DXY", "SPY"]
        query = (q or "XAUUSD OR gold price OR DXY OR VIX OR US yields").strip()

        all_items: list[dict] = []

        # 1) Yahoo Finance RSS
        try:
            rss_url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
            params = {"s": ",".join(syms), "region": "US", "lang": "en-US"}
            ry = await _client.get(rss_url, params=params, timeout=10)
            ry.raise_for_status()
            root = ET.fromstring(ry.text)
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = it.findtext("pubDate") or it.findtext("published") or it.findtext("dc:date")
                ts = None
                if pub:
                    try:
                        ts = to_utc_ms(dateparser.parse(pub))
                    except Exception:
                        ts = None
                src = "yahoo"
                all_items.append({"title": title, "link": link, "ts": ts, "src": src})
        except Exception:
            pass

        # 2) Google News RSS (query)
        try:
            g_url = "https://news.google.com/rss/search"
            gp = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
            rg = await _client.get(g_url, params=gp, timeout=10)
            rg.raise_for_status()
            root = ET.fromstring(rg.text)
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                pub = it.findtext("pubDate")
                ts = None
                if pub:
                    try:
                        ts = to_utc_ms(dateparser.parse(pub))
                    except Exception:
                        ts = None
                all_items.append({"title": title, "link": link, "ts": ts, "src": "google"})
        except Exception:
            pass

        # 3) GDELT Doc API (JSON)
        try:
            gd_url = "https://api.gdeltproject.org/api/v2/doc/doc"
            gp = {"query": query, "mode": "ArtList", "format": "json", "maxrecords": "50", "timespan": "1d"}
            rgd = await _client.get(gd_url, params=gp, timeout=12)
            rgd.raise_for_status()
            j = rgd.json()
            arts = (j or {}).get("articles") or (j or {}).get("artlist") or []
            for a in arts:
                try:
                    title = str(a.get("title") or "").strip()
                    link = str(a.get("url") or a.get("seendatelink") or a.get("sourceurl") or "").strip()
                    ts = None
                    if a.get("seendate"):
                        try:
                            ts = to_utc_ms(dateparser.parse(str(a.get("seendate"))))
                        except Exception:
                            ts = None
                    src = a.get("sourcecountry") or a.get("domain") or "gdelt"
                    if title or link:
                        all_items.append({"title": title, "link": link, "ts": ts, "src": str(src)})
                except Exception:
                    continue
        except Exception:
            pass

        # Deduplicate by link or title
        seen = set()
        deduped: list[dict] = []
        for it in all_items:
            key = (it.get("link") or it.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(it)

        # Sort by ts desc when available
        try:
            deduped.sort(key=lambda x: x.get("ts") or 0, reverse=True)
        except Exception:
            pass

        lim = max(1, min(100, int(limit)))
        out = {"items": deduped[:lim]}
        _cache_put(cache_key, out)
        return out
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"news: {e}")


# ---------------- Signals + Ledger (MVP) ----------------
@router.get("/v1/signals/recent")
async def v1_signals_recent(limit: int = 20):
    try:
        if not _signals_store:
            # bootstrap with one generated candidate
            gen = await _generate_signal("XAUUSD")
            if gen:
                _signals_store.append(gen)
        rows = sorted(_signals_store, key=lambda s: s.get("ts", 0), reverse=True)
        return rows[: max(1, min(100, limit))]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"signals: {e}")


@router.get("/v1/signals/pro/last")
async def v1_signals_pro_last():
    """
    Get latest institutional-lite pro signal from microstructure engine.
    
    Returns signal based on:
    - Order Flow Imbalance (OFI)
    - Microprice (pressure-adjusted fair value)
    - Variance ratio (regime classification)
    - Spread hygiene
    - Optional sweep context (PDH/PDL)
    
    Entry pricing uses limit orders inside spread to reduce adverse selection.
    """
    try:
        if not _micro_enabled:
            return {
                "enabled": False,
                "message": "Microstructure engine not available"
            }
        
        if _last_pro_signal is None:
            return {
                "enabled": True,
                "signal": None,
                "diagnostics": _micro_engine.diagnostics() if _micro_engine else {}
            }
        
        return {
            "enabled": True,
            "signal": _last_pro_signal,
            "diagnostics": _micro_engine.diagnostics() if _micro_engine else {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pro signals: {e}")


@router.get("/v1/signals/pro/diagnostics")
async def v1_signals_pro_diagnostics():
    """Get microstructure engine diagnostics and current features."""
    try:
        if not _micro_enabled or _micro_engine is None:
            return {"enabled": False}
        
        diag = _micro_engine.diagnostics()
        features = _micro_engine.features()
        
        return {
            "enabled": True,
            "diagnostics": diag,
            "features": features,
            "last_signal_ts": _last_pro_signal.get("ts") if _last_pro_signal else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"diagnostics: {e}")


@router.get("/v1/metrics/ledger")
async def v1_metrics_ledger(limit: int = 200):
    try:
        # simple stub rows for UI wiring
        now = now_utc_ms()
        rows = [
            {
                "signal_id": "sig-demo-1",
                "open_ts": now - 90 * 60 * 1000,
                "close_ts": now - 12 * 60 * 1000,
                "open_price": 2415.2,
                "close_price": 2420.7,
                "mae": -3.8,
                "mfe": 9.6,
                "outcome_r": 0.7,
                "slippage": 1.0,
                "spread": 20.0,
                "reason_close": "TP1 hit",
            },
            {
                "signal_id": "sig-demo-2",
                "open_ts": now - 240 * 60 * 1000,
                "close_ts": now - 160 * 60 * 1000,
                "open_price": 2432.4,
                "close_price": 2425.0,
                "mae": -7.2,
                "mfe": 5.1,
                "outcome_r": -0.5,
                "slippage": 1.4,
                "spread": 24.0,
                "reason_close": "Time stop",
            },
        ]
        return rows[: max(1, min(500, limit))]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ledger: {e}")


@router.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
    await ws.accept()
    _signal_ws_clients.add(ws)
    try:
        while True:
            # keepalive; we do not expect client messages
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _signal_ws_clients.discard(ws)


async def _broadcast_signal(sig: dict):
    if not _signal_ws_clients:
        return
    data = json.dumps(sig)
    send_tasks = []
    for c in list(_signal_ws_clients):
        send_tasks.append(c.send_text(data))
    try:
        await asyncio.gather(*send_tasks, return_exceptions=True)
    except Exception:
        pass


@router.post("/v1/signals/generate", status_code=201)
async def v1_signals_generate(symbol: str = "XAUUSD"):
    try:
        sig = await _generate_signal(symbol)
        if not sig:
            return {"status": "noop"}
        _signals_store.append(sig)
        await _broadcast_signal(sig)
        return {"ok": True, "id": sig.get("id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"signals/generate: {e}")


async def _latest_mid(symbol: str = "XAUUSD") -> tuple[float | None, int | None]:
    """Return (mid, spread_pts) using primary providers with short TTL caches."""
    try:
        bid = ask = last = None
        try:
            gq = await cached_goldapi_quote(symbol, ttl_sec=5)
            bid = gq.get("bid"); ask = gq.get("ask"); last = gq.get("last") or gq.get("price")
        except Exception:
            pass
        if bid is None or ask is None:
            try:
                tq = await cached_twelvedata_quote(symbol, ttl_sec=5)
                bid = bid or tq.get("bid"); ask = ask or tq.get("ask"); last = last or tq.get("last")
            except Exception:
                pass
        mid = None
        if bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2.0
            except Exception:
                mid = None
        if mid is None:
            try:
                mid = float(last) if last is not None else None
            except Exception:
                mid = None
        spread_pts = None
        try:
            if bid is not None and ask is not None:
                spread_pts = int(round(max(0.0, float(ask) - float(bid)) * 100))
        except Exception:
            spread_pts = None
        return mid, spread_pts
    except Exception:
        return None, None


async def _generate_signal(symbol: str = "XAUUSD") -> dict | None:
    """Build a higher-quality signal for XAUUSD using fresh drivers, intraday ATR, and quality gating."""
    try:
        # Drivers and weights
        drivers = await _compute_drivers_payload()
        # Map available z-values with correct sign for gold
        dz = drivers.get("dxy", {})
        vz = drivers.get("vix", {})
        rz = drivers.get("real", drivers.get("real10y", {}))
        noz = drivers.get("nominal", drivers.get("nominal10y", {}))
        rk = drivers.get("risk_on", {})
        mom = drivers.get("mom", {})
        do_ctx = drivers.get("do_ctx", {})

        # Default zeros if missing
        dxy_v = float(dz.get("z") or 0.0) * -1.0
        real_v = float(rz.get("z") or 0.0) * -1.0
        vix_v = float(vz.get("z") or 0.0) * 1.0
        mom_v = float(mom.get("z") or 0.0)
        risk_v = float(rk.get("z") or 0.0)
        nom_v = float(noz.get("z") or 0.0) * -1.0
        do_v = float(do_ctx.get("z") or 0.0)

        # Weights (aligned with rebalanced nowcast)
        w_dxy, w_real, w_vix, w_mom, w_risk, w_nom, w_do = 0.50, 0.20, 0.10, 0.35, 0.15, 0.05, 0.10
        lin = (
            w_dxy * dxy_v +
            w_real * real_v +
            w_vix * vix_v +
            w_mom * mom_v +
            w_risk * risk_v +
            w_nom * nom_v +
            w_do * do_v
        )
        try:
            import math
            p_up = 1.0 / (1.0 + math.exp(-lin))
        except Exception:
            p_up = 0.5

        # Confidence caps: degrade when any key drivers stale
        stale_keys = [dz, rz, vz, mom, rk]
        any_stale = any(k.get("fresh") is False for k in stale_keys)
        if any_stale:
            # pull towards neutral if stale
            p_up = 0.5 + (p_up - 0.5) * 0.8

        side = "LONG" if p_up >= 0.58 else ("SHORT" if p_up <= 0.42 else None)
        if side is None:
            return None  # no edge

        last, spread_pts = await _latest_mid(symbol)
        if last is None:
            return None

        # Quality gate using spread; clamp confidence when degraded
        if spread_pts is not None and spread_pts > 30:
            p_up = 0.5 + (p_up - 0.5) * 0.6
            if spread_pts > 60:
                return None  # too wide to act

        # Intraday ATR20 from 1m candles when available; fallback to proxy
        atr20 = None
        try:
            candles, _ = await get_candles(symbol)
            atr20 = _wilder_atr20_from_ohlc1m(candles)
        except Exception:
            atr20 = None
        if atr20 is None or atr20 <= 0:
            atr20 = max(0.1, 0.008 * float(last))  # ~0.8% fallback

        sl_dist = 0.75 * atr20
        tp1_dist = 1.2 * atr20
        entry = float(last)
        if side == "LONG":
            sl = entry - sl_dist
            tp1 = entry + tp1_dist
            tp2 = entry + 1.8 * atr20
        else:
            sl = entry + sl_dist
            tp1 = entry - tp1_dist
            tp2 = entry - 1.8 * atr20

        # Session context (prefer London/NY)
        session_hint = None
        try:
            anchor = session_day_anchor(ny_now())
            sydney, tokyo, london, newyork = build_sessions_windows(anchor)
            now_ms = now_utc_ms()
            if london[0] <= now_ms < london[1]:
                session_hint = "London"
            elif newyork[0] <= now_ms < newyork[1]:
                session_hint = "New York"
            elif tokyo[0] <= now_ms < tokyo[1]:
                session_hint = "Asia"
            else:
                session_hint = "Off"
            # trim target distances in Asia
            if session_hint == "Asia":
                tp1 = entry + (tp1 - entry) * (0.85 if side == "LONG" else 1.0)
                tp1 = entry - (entry - tp1) * (0.85 if side == "SHORT" else 1.0)
        except Exception:
            session_hint = None

        # Build contributions and reasons
        contribs = [
            ("DXY", w_dxy * dxy_v, dz.get("fresh", True)),
            ("Real Yields", w_real * real_v, rz.get("fresh", True)),
            ("VIX", w_vix * vix_v, vz.get("fresh", True)),
            ("Momentum", w_mom * mom_v, mom.get("fresh", True)),
            ("Risk-on", w_risk * risk_v, rk.get("fresh", True)),
            ("Nominal", w_nom * nom_v, noz.get("fresh", True)),
            ("DO context", w_do * do_v, do_ctx.get("fresh", True)),
        ]
        contribs.sort(key=lambda x: abs(x[1]), reverse=True)
        reasons = []
        for lbl, c, fr in contribs[:3]:
            try:
                reasons.append(f"{lbl} {('' if c>=0 else '')}{round(c,2)}{' (stale)' if not fr else ''}")
            except Exception:
                reasons.append(lbl)
        if session_hint:
            reasons.append(f"Session {session_hint}")
        if spread_pts is not None:
            reasons.append(f"Spread {spread_pts}pt")

        sig = {
            "id": f"sig-{now_utc_ms()}-{side.lower()}",
            "ts": now_utc_ms(),
            "symbol": symbol,
            "side": side,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "time_stop_min": 60,
            "confidence": round(float(p_up), 4),
            "regime": "NOWCAST-ML",
            "reasons": reasons,
            "reason": (reasons[0] if reasons else None),
            "status": "OPEN",
        }
        return sig
    except Exception:
        return None

async def _alpha_fx_daily_pair_latest_prev(from_symbol: str, to_symbol: str) -> Optional[Tuple[float, float]]:
    if not ALPHA_KEY:
        return None
    key = ("alpha_fx_daily", f"{from_symbol}/{to_symbol}")
    hit = _cache_get(key, ttl_ms=6 * 60 * 60 * 1000)
    if hit is not None:
        return hit
    params = {
        "function": "FX_DAILY",
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "outputsize": "compact",
        "apikey": ALPHA_KEY,
    }
    r = await _client.get(ALPHA_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    series = (j or {}).get("Time Series FX (Daily)")
    if not isinstance(series, dict) or not series:
        return None
    dates = sorted(series.keys(), reverse=True)
    if len(dates) < 2:
        return None
    try:
        last = float(series[dates[0]].get("4. close"))
        prev = float(series[dates[1]].get("4. close"))
        _cache_put(key, (last, prev))
        return (last, prev)
    except Exception:
        return None

async def alpha_dxy_pct() -> Optional[float]:
    """Compute DXY percent change via Alpha Vantage FX_DAILY pairs; fallback to UUP percent if needed."""
    if not ALPHA_KEY:
        return None
    DXY_CONST = 50.14348112
    WEIGHTS = [
        ("EUR", "USD", -0.576),
        ("USD", "JPY", 0.136),
        ("GBP", "USD", -0.119),
        ("USD", "CAD", 0.091),
        ("USD", "SEK", 0.042),
        ("USD", "CHF", 0.036),
    ]
    try:
        tasks = [_alpha_fx_daily_pair_latest_prev(fr, to) for fr, to, _ in WEIGHTS]
        pairs = await asyncio.gather(*tasks)
        if any(p is None for p in pairs):
            # ETF proxy percent change fallback
            return await fetch_alpha_global_quote_pct("UUP")
        def dxy_from(values: List[Tuple[float, float]], idx: int) -> float:
            v = DXY_CONST
            for (fr, to, w), pair in zip(WEIGHTS, values):
                rate = pair[idx]
                v *= rate ** w
            return v
        last_val = dxy_from(pairs, 0)
        prev_val = dxy_from(pairs, 1)
        if prev_val == 0:
            return None
        return ((last_val - prev_val) / prev_val) * 100.0
    except Exception:
        try:
            return await fetch_alpha_global_quote_pct("UUP")
        except Exception:
            return None

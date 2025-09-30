from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import time
import asyncio
import httpx
from datetime import datetime, timedelta, timezone
import pytz
from typing import Optional, Tuple, Dict, Any
import csv
from io import StringIO
import math
import lzma
import struct

NY = pytz.timezone("America/New_York")
YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
ALPHA_BASE = "https://www.alphavantage.co/query"
ALPHA_KEY = os.getenv("ALPHAVANTAGE_API_KEY")
TWELVE_BASE = "https://api.twelvedata.com/time_series"
TWELVE_KEY = os.getenv("TWELVEDATA_API_KEY")

app = FastAPI()
# Allow mobile clients to call the API (adjust origins if you want to restrict)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
_client: Optional[httpx.AsyncClient] = None
_cache: Dict[Tuple[str, str], Tuple[int, Any]] = {}


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


async def fetch_yahoo(symbol: str):
    # Map app symbol to Yahoo
    ticker = "XAUUSD=X" if symbol.upper() == "XAUUSD" else symbol
    url = f"{YF_BASE}{ticker}"
    params = {"interval": "1m", "range": "1d"}
    r = await _client.get(url, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    result = j.get("chart", {}).get("result", [])
    if not result:
        raise RuntimeError("Yahoo: no result")
    res = result[0]
    ts = res.get("timestamp", [])
    quote = res.get("indicators", {}).get("quote", [{}])[0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    candles = []
    for i in range(min(len(ts), len(opens), len(highs), len(lows), len(closes))):
        if opens[i] is None or highs[i] is None or lows[i] is None or closes[i] is None:
            continue
        candles.append({
            "t": int(ts[i]) * 1000,
            "o": float(opens[i]),
            "h": float(highs[i]),
            "l": float(lows[i]),
            "c": float(closes[i]),
        })
    if not candles:
        raise RuntimeError("Yahoo: empty candles")
    last_price = candles[-1]["c"]
    return candles, last_price


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
            candles.append({
                "t": to_utc_ms(aware),
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
            })
        except Exception:
            continue
    if not candles:
        raise RuntimeError("Stooq: empty series")
    candles.sort(key=lambda x: x["t"])
    last_price = candles[-1]["c"]
    return candles, last_price


async def fetch_twelvedata(symbol: str):
    if not TWELVE_KEY:
        raise RuntimeError("TwelveData key missing")
    # TwelveData expects symbols like XAU/USD
    sym = "XAU/USD" if symbol.upper() == "XAUUSD" else symbol
    params = {
        "symbol": sym,
        "interval": "5min",
        "outputsize": "390",  # roughly 2 trading days of 5m bars
        "apikey": TWELVE_KEY,
        "timezone": "UTC",
        "format": "JSON",
        "order": "ASC",
    }
    r = await _client.get(TWELVE_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
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
    sym = "XAU/USD" if symbol.upper() == "XAUUSD" else symbol
    url = "https://api.twelvedata.com/quote"
    r = await _client.get(url, params={"symbol": sym, "apikey": TWELVE_KEY}, timeout=10)
    r.raise_for_status()
    j = r.json()
    if "bid" not in j and "ask" not in j and "price" not in j:
        raise RuntimeError(f"TwelveData quote: {j}")
    def _to_f(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    return {
        "bid": _to_f(j.get("bid")),
        "ask": _to_f(j.get("ask")),
        "last": _to_f(j.get("price") or j.get("close")),
    }


async def fetch_dukascopy(symbol: str, hours_back: int = 30):
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
        r = await _client.get(url, timeout=15)
        if r.status_code != 200 or not r.content:
            return
        try:
            raw = lzma.decompress(r.content)
        except Exception:
            return
        rec = 20
        for i in range(0, len(raw) - (len(raw) % rec), rec):
            try:
                tms, ask, bid, av, bv = struct.unpack(">Iffff", raw[i:i+rec])
            except Exception:
                continue
            ts_ms = int(dt_hour.timestamp() * 1000) + int(tms)
            mid = (ask + bid) / 2.0
            ticks.append((ts_ms, mid))

    # Fetch newest to oldest so we can early stop when enough data
    for h in range(hours_back):
        await fetch_hour(now_utc - timedelta(hours=h))

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
    if ts_payload and (now_utc_ms() - ts_payload[0] < 60_000):
        return ts_payload[1]
    try:
        payload = await fetch_yahoo(symbol)
    except Exception as e_yahoo:
        try:
            candles_td, last_td = await fetch_twelvedata(symbol)
            # Prefer TD quote mid; sanity-check vs TD candle close to avoid stale spikes
            last_sane = last_td
            try:
                q = await fetch_twelvedata_quote(symbol)
                candidate = None
                if q.get("bid") and q.get("ask"):
                    candidate = (q["bid"] + q["ask"]) / 2.0
                elif q.get("last"):
                    candidate = q["last"]
                if candidate is not None:
                    c_last = candles_td[-1]["c"] if candles_td else candidate
                    if abs(candidate - c_last) <= 5.0:  # within $5 → accept
                        last_sane = candidate
                    else:
                        # Try Dukascopy if TD quote looks off
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
            try:
                payload = await fetch_dukascopy(symbol)
            except Exception as e_duka:
                try:
                    payload = await fetch_stooq(symbol)
                except Exception as e_stooq:
                    try:
                        payload = await fetch_alpha(symbol)
                    except Exception as e_alpha:
                        raise RuntimeError(f"Yahoo failed: {e_yahoo}; TwelveData failed: {e_twelve}; Dukascopy failed: {e_duka}; Stooq failed: {e_stooq}; Alpha failed: {e_alpha}")
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


@app.on_event("startup")
async def startup():
    global _client
    _client = httpx.AsyncClient(headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    })


@app.on_event("shutdown")
async def shutdown():
    await _client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/levels/intraday")
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


@app.get("/levels/intraday/sessions")
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


@app.get("/market/ohlc24h")
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


@app.get("/calendar/upcoming")
async def calendar_upcoming(ccy: str = "USD", hours: int = 72):
    # Simple stub: an event in 42 minutes from now
    now_ms = now_utc_ms()
    in_ms = 42 * 60 * 1000
    event_time = now_ms + in_ms
    return {
        "next_red": {
            "title": f"{ccy} CPI",
            "impact": "high",
            "time_utc": datetime.utcfromtimestamp(event_time / 1000).replace(tzinfo=timezone.utc).isoformat(),
            "lock_window": {
                "start_utc": datetime.utcfromtimestamp((event_time - 15*60*1000) / 1000).replace(tzinfo=timezone.utc).isoformat(),
                "end_utc": datetime.utcfromtimestamp((event_time + 15*60*1000) / 1000).replace(tzinfo=timezone.utc).isoformat(),
            }
        }
    }


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
    try:
        candles, last_price = await fetch_yahoo(symbol)
        return {"candles": candles, "last": last_price}
    except Exception:
        return None


@app.get("/home")
async def home():
    try:
        # XAU intraday (with multi-provider fallback via get_candles)
        candles, last_price = await get_candles("XAUUSD")

        # Build arrays
        ts = [c["t"] for c in candles]
        o = [c["o"] for c in candles]
        h = [c["h"] for c in candles]
        l = [c["l"] for c in candles]
        cvals = [c["c"] for c in candles]

        # 24h window stats
        end_ms = now_utc_ms()
        start_ms = end_ms - 24 * 60 * 60 * 1000
        win = [i for i, t in enumerate(ts) if t >= start_ms]
        if win:
            i0 = win[0]
        else:
            i0 = max(0, len(ts) - 1)
        high24 = max(h[i0:]) if i0 < len(h) else None
        low24 = min(l[i0:]) if i0 < len(l) else None
        base = cvals[i0] if i0 < len(cvals) and cvals[i0] else None
        change24 = (last_price - base) if (base is not None) else None
        pct24 = ((change24 / base) * 100.0) if (base and base != 0) else None

        # SAST DO
        do_price = _find_sast_midnight_open(candles)

        # Drivers via YF (best effort)
        dxy = await _fetch_intraday_yf_series("^DXY")
        vix = await _fetch_intraday_yf_series("^VIX")
        tnx = await _fetch_intraday_yf_series("^TNX")
        drivers = []
        if dxy:
            drivers.append({"key": "dxyZ", "value": _z_from_tail([c["c"] for c in dxy["candles"]])})
        if tnx:
            drivers.append({"key": "realZ", "value": _z_from_tail([c["c"] / 10.0 for c in tnx["candles"]])})
        if vix:
            drivers.append({"key": "vixZ", "value": _z_from_tail([c["c"] for c in vix["candles"]])})

        # Calendar next red using existing stub
        cal = await calendar_upcoming("USD", 72)

        payload = {
            "price": {
                "last": last_price,
                "change24h": change24,
                "pct24h": pct24,
                "high24h": high24,
                "low24h": low24,
                "updatedAt": end_ms,
            },
            "levels": {
                "do": {"price": do_price},
                "pdh": {"price": max(h) if h else None},
                "pdl": {"price": min(l) if l else None},
            },
            "metrics": {
                "gap_pct": ((last_price - do_price) / do_price * 100.0) if (do_price and do_price != 0) else None,
                "nowcast": {
                    "drivers": drivers,
                    "model_id": "stub-000",
                    "updated_at": end_ms,
                },
            },
            "calendar": {"next_red": cal.get("next_red")} if isinstance(cal, dict) else {},
            "quality": {"state": "OK"},
        }
        return payload
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


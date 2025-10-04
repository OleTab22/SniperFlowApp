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
PRICE_SOURCE = os.getenv("PRICE_SOURCE", "auto").lower()  # auto|yahoo|twelvedata|dukascopy|stooq|alpha
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_KEY = os.getenv("FRED_API_KEY")

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
                # Dukascopy stores 5 big-endian 32-bit integers
                # [ms, ask_int, bid_int, ask_vol_int, bid_vol_int]
                tms, ask_i, bid_i, _av_i, _bv_i = struct.unpack(">IIIII", raw[i:i+rec])
            except Exception:
                continue
            ts_ms = int(dt_hour.timestamp() * 1000) + int(tms)
            # Detect scale to bring price into realistic gold range
            mid_i = (ask_i + bid_i) / 2.0
            price = None
            for s in (100000.0, 10000.0, 1000.0, 100.0, 10.0, 1.0):
                v = mid_i / s
                if 500.0 <= v <= 10000.0:
                    price = v
                    break
            if price is None:
                # Fallback: assume 1000 scale for metals
                price = mid_i / 1000.0
            ticks.append((ts_ms, float(price)))

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
    # Explicit source override via env for diagnostics
    if PRICE_SOURCE in ("yahoo", "twelvedata", "dukascopy", "stooq", "alpha"):
        if PRICE_SOURCE == "yahoo":
            return await fetch_yahoo(symbol)
        if PRICE_SOURCE == "twelvedata":
            return await fetch_twelvedata(symbol)
        if PRICE_SOURCE == "dukascopy":
            return await fetch_dukascopy(symbol)
        if PRICE_SOURCE == "stooq":
            return await fetch_stooq(symbol)
        if PRICE_SOURCE == "alpha":
            return await fetch_alpha(symbol)

    # Auto strategy with sanity checks
    try:
        payload = await fetch_yahoo(symbol)
    except Exception as e_yahoo:
        try:
            candles_td, last_td = await fetch_twelvedata(symbol)
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
            # App expects epoch seconds as a string
            "time_utc": str(int(event_time // 1000)),
            "lock_window": {
                "start_utc": str(int((event_time - 15*60*1000) // 1000)),
                "end_utc": str(int((event_time + 15*60*1000) // 1000)),
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


async def _yf_chart_with_volume(symbol: str, interval: str, range_: str) -> Optional[Dict[str, Any]]:
    """Fetch Yahoo chart with volume when available; returns dict with 'candles'."""
    try:
        url = f"{YF_BASE}{symbol}"
        params = {"interval": interval, "range": range_}
        r = await _client.get(url, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        result = j.get("chart", {}).get("result", [])
        if not result:
            return None
        res = result[0]
        ts = res.get("timestamp", [])
        quote = res.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        vols = quote.get("volume", []) or []
        n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
        candles = []
        for i in range(n):
            if opens[i] is None or highs[i] is None or lows[i] is None or closes[i] is None:
                continue
            c = {
                "t": int(ts[i]) * 1000,
                "o": float(opens[i]),
                "h": float(highs[i]),
                "l": float(lows[i]),
                "c": float(closes[i]),
            }
            if i < len(vols) and vols[i] is not None:
                try:
                    c["v"] = float(vols[i])
                except Exception:
                    pass
            candles.append(c)
        if not candles:
            return None
        return {"candles": candles}
    except Exception:
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

        # SAST DO
        do_price = _find_sast_midnight_open(candles)

        # Drivers via YF (best effort)
        dxy = await _fetch_intraday_yf_series("^DXY")
        vix = await _fetch_intraday_yf_series("^VIX")
        tnx = await _fetch_intraday_yf_series("^TNX")
        drivers = []
        if dxy:
            last_ts = dxy["candles"][-1]["t"] if dxy["candles"] else 0
            stale = (end_ms - last_ts) > (10 * 60 * 1000)
            drivers.append({"key": "dxyZ", "value": _z_from_tail([c["c"] for c in dxy["candles"]]), "stale": stale})
        if tnx:
            last_ts = tnx["candles"][-1]["t"] if tnx["candles"] else 0
            stale = (end_ms - last_ts) > (10 * 60 * 1000)
            drivers.append({"key": "realZ", "value": _z_from_tail([c["c"] / 10.0 for c in tnx["candles"]]), "stale": stale})
        if vix:
            last_ts = vix["candles"][-1]["t"] if vix["candles"] else 0
            stale = (end_ms - last_ts) > (10 * 60 * 1000)
            drivers.append({"key": "vixZ", "value": _z_from_tail([c["c"] for c in vix["candles"]]), "stale": stale})

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
        except Exception:
            volume_percentile = None

        # Session-aware PDH/PDL (previous session high/low using 17:00 ET anchor)
        nyt = ny_now()
        anchor = session_day_anchor(nyt)
        prev_start = anchor - timedelta(days=1)
        prev_end = anchor
        prev_window = filter_candles(candles, to_utc_ms(prev_start), to_utc_ms(prev_end))
        prev_levels = compute_levels_for_window(prev_window)

        # Simple nowcast based on driver z-scores (logistic transform)
        dxy_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "dxyZ"), 0.0)
        real_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "realZ"), 0.0)
        vix_z = next((d.get("value", 0.0) for d in drivers if d.get("key") == "vixZ"), 0.0)
        # Momentum driver based on today's move vs intraday range
        mom = 0.0
        try:
            if intraday_range and intraday_range > 0 and base_do is not None:
                mom = (float(last_price) - float(base_do)) / float(intraday_range)
                mom = max(-1.0, min(1.0, mom))
        except Exception:
            mom = 0.0
        # Mirror signs similar to client-side model
        real_z_c = max(-1.5, min(1.5, -real_z))
        dxy_z_c = -dxy_z
        vix_z_c = vix_z
        term_dxy = 0.60 * dxy_z_c
        term_real = 0.20 * real_z_c
        term_vix = 0.20 * vix_z_c
        term_mom = 0.30 * mom
        logit = 0.0 + term_dxy + term_real + term_vix + term_mom
        p_up = 1.0 / (1.0 + math.exp(-logit))
        direction = "bull" if p_up >= 0.5 else "bear"
        confidence = max(p_up, 1.0 - p_up)

        # Add contribution fractions for driver chips
        sum_abs = sum(abs(x) for x in (term_dxy, term_real, term_vix, term_mom)) or 1.0
        for d in drivers:
            if d["key"] == "dxyZ":
                d["contribution"] = term_dxy / sum_abs
            elif d["key"] == "realZ":
                d["contribution"] = term_real / sum_abs
            elif d["key"] == "vixZ":
                d["contribution"] = term_vix / sum_abs
        drivers.append({"key": "mom", "value": mom, "stale": False, "contribution": term_mom / sum_abs})

        # Quote for bid/ask and spread/quality
        bid = None
        ask = None
        spread_pts = None
        try:
            q = await fetch_twelvedata_quote("XAUUSD")
            bid = q.get("bid")
            ask = q.get("ask")
            if bid and ask:
                last_price = (bid + ask) / 2.0
                spread = max(0.0, float(ask) - float(bid))
                spread_pts = int(round(spread * 100))  # ~0.01 per point
        except Exception:
            pass

        # Quality state from spread/latency (best effort)
        latency_ms = 0
        if spread_pts is not None:
            if spread_pts <= 20 and latency_ms <= 300:
                q_state = "OK"
            elif spread_pts <= 30 and latency_ms <= 600:
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

        payload = {
            "price": {
                "last": last_price,
                # These fields are computed for current SAST day
                "change24h": change_day,
                "pct24h": pct_day,
                "high24h": high_day,
                "low24h": low_day,
                "updatedAt": end_ms,
                "closes": [c["c"] for c in intraday] if 'intraday' in locals() else None,
                "bid": bid,
                "ask": ask,
            },
            "levels": {
                "do": {"price": do_price},
                # Previous session high/low
                "pdh": {"price": prev_levels["PDH"]},
                "pdl": {"price": prev_levels["PDL"]},
            },
            "metrics": {
                "gap_pct": ((last_price - do_price) / do_price * 100.0) if (do_price and do_price != 0) else None,
                "range_to_atr20": range_to_atr20,
                "volume_percentile": volume_percentile,
                "activity_index": activity_index,
                "nowcast": {
                    "direction": direction,
                    "confidence": confidence,
                    "window_min": 60,
                    "drivers": drivers + [{"key": "mom", "value": mom}],
                    "model_id": "stub-000",
                    "updated_at": end_ms,
                },
            },
            "calendar": {"next_red": cal.get("next_red")} if isinstance(cal, dict) else {},
            "sessions": {"overlap_with_ny": (8 <= ny_now().hour < 12), "current": current_session},
            "quality": {"state": q_state, "spread_pts": spread_pts, "latency_ms": latency_ms},
            "gates": {"plan_lock": False, "reason": None, "news_lock": news_lock},
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
        return payload
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------------- v1 DRIVERS / NOWCAST / FEATURES ----------------

def _staleness(last_ts_ms: int, now_ms: int, fresh_threshold_min: int = 10) -> tuple[bool, int]:
    """Return (fresh, staleSec) given last data timestamp and 'now'."""
    if not last_ts_ms:
        return (False, 0)
    delta_s = max(0, (now_ms - last_ts_ms) // 1000)
    return (delta_s <= fresh_threshold_min * 60, delta_s)


async def _compute_drivers_payload() -> Dict[str, Any]:
    """
    Internal helper that pulls intraday ^DXY, ^VIX, ^TNX from Yahoo,
    computes z-scores, freshness, and returns the drivers dict.
    Signs: for gold, negative DXY and negative real yields are supportive.
    """
    end_ms = now_utc_ms()
    out = {}

    # DXY fallback chain: DX-Y.NYB -> DX=F -> UUP -> ^DXY
    dxy = await _fetch_intraday_yf_series_multi(["DX-Y.NYB", "DX=F", "UUP", "^DXY"])
    vix = await _fetch_intraday_yf_series("^VIX")
    tnx = await _fetch_intraday_yf_series("^TNX")  # 10y nominal yield *10
    es = await _fetch_intraday_yf_series("ES=F")   # S&P futures for risk-on

    if dxy and dxy.get("candles"):
        last_ts = dxy["candles"][-1]["t"]
        fresh, stale = _staleness(last_ts, end_ms)
        z = _z_from_tail([c["c"] for c in dxy["candles"]])
        # invert sign for gold tilt
        out["dxy"] = {"z": float(-z), "w": 0.35, "fresh": fresh, "staleSec": stale, "sym": dxy.get("symbol")}
    else:
        out["dxy"] = {"z": 0.0, "w": 0.35, "fresh": False, "staleSec": None}

    if vix and vix["candles"]:
        last_ts = vix["candles"][-1]["t"]
        fresh, stale = _staleness(last_ts, end_ms)
        z = _z_from_tail([c["c"] for c in vix["candles"]])
        out["vix"] = {"z": float(z), "w": 0.20, "fresh": fresh, "staleSec": stale}
    else:
        out["vix"] = {"z": 0.0, "w": 0.20, "fresh": False, "staleSec": None}

    if tnx and tnx.get("candles"):
        last_ts = tnx["candles"][-1]["t"]
        fresh, stale = _staleness(last_ts, end_ms)
        # TNX is ~10x yield in %, divide by 10; invert sign for gold
        z = _z_from_tail([c["c"]/10.0 for c in tnx["candles"]])
        out["nominal10y"] = {"z": float(-z), "w": 0.20, "fresh": fresh, "staleSec": stale}
    else:
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
    if es and es.get("candles") and vix and vix.get("candles"):
        last_ts = min(es["candles"][-1]["t"], vix["candles"][-1]["t"])
        fresh, stale = _staleness(last_ts, end_ms)
        z_es = _z_from_tail([c["c"] for c in es["candles"]])
        z_vix = _z_from_tail([c["c"] for c in vix["candles"]])
        z_risk = 0.60 * z_es - 0.40 * z_vix
        out["risk_on"] = {"z": float(z_risk), "w": 0.10, "fresh": fresh, "staleSec": stale}
    else:
        out["risk_on"] = {"z": 0.0, "w": 0.10, "fresh": False, "staleSec": None}

    return out


@app.get("/v1/drivers")
async def v1_drivers():
    """
    Macro drivers used by the client: DXY (−), real10y (−), VIX (+).
    Returns z-scores, weights, freshness flags, and staleness seconds.
    """
    try:
        return await _compute_drivers_payload()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"drivers: {e}")


@app.get("/v1/nowcast")
async def v1_nowcast():
    """
    Simple nowcast score in [-100, 100], based on drivers with signs:
      logit = 0.60 * dxy.z  +  0.20 * real10y.z  +  0.20 * vix.z
      score = clip(logit, -1..1) * 100
    (dxy.z and real10y.z are already sign-adjusted inside _compute_drivers_payload)
    """
    try:
        drv = await _compute_drivers_payload()
        # Apply staleness decay: w' = w * exp(-staleSec / tau)
        import math as _m
        tau = 45 * 60  # 45 minutes
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


@app.get("/v1/features")
async def v1_features(symbol: str = "XAUUSD"):
    """
    Feature panel for the app: gap %, ATR20x proxy, activity, volume percentile,
    24h high/low, and quality from bid/ask spread when available.
    """
    try:
        candles, last_price = await get_candles(symbol)

        end_ms = now_utc_ms()
        # 24h window
        start_ms = end_ms - 24 * 60 * 60 * 1000
        w = [c for c in candles if c["t"] >= start_ms]
        h24 = max((c["h"] for c in w), default=None)
        l24 = min((c["l"] for c in w), default=None)

        # Gap% per spec: (today_open - prev_close)/prev_close*100 using UTC day
        now_utc = datetime.now(timezone.utc)
        today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_midnight = today_midnight - timedelta(days=1)
        today_w = filter_candles(candles, to_utc_ms(today_midnight), to_utc_ms(today_midnight + timedelta(days=1)))
        prev_w = filter_candles(candles, to_utc_ms(prev_midnight), to_utc_ms(today_midnight))
        today_open = today_w[0]["o"] if today_w else None
        prev_close = prev_w[-1]["c"] if prev_w else None
        gap_pct = None
        if prev_close and prev_close != 0 and today_open:
            gap_pct = (today_open - prev_close) / prev_close * 100.0

        # Intraday range & ATR20 proxy
        tz_sast = pytz.timezone("Africa/Johannesburg")
        midnight_local = datetime.now(tz_sast).replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_ms = int(midnight_local.astimezone(timezone.utc).timestamp() * 1000)
        intraday = [c for c in candles if c["t"] >= midnight_ms]
        intraday_hi = max((c["h"] for c in intraday), default=None)
        intraday_lo = min((c["l"] for c in intraday), default=None)
        intraday_range = (intraday_hi - intraday_lo) if (intraday_hi is not None and intraday_lo is not None) else None

        atr20x = None
        if intraday_range is not None and last_price:
            atr20_proxy = max(1e-9, 0.01 * float(last_price))
            atr20x = float(intraday_range) / atr20_proxy

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

        # Volume percentile via GC=F (futures) vs 20-day median-by-minute (5m cadence)
        volPct = None
        try:
            volPct = await _volume_percentile_gcf()
        except Exception:
            volPct = None

        # Quality from bid/ask spread if available
        quality = "OK"
        try:
            q = await fetch_twelvedata_quote(symbol)
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

        return {
            "gapPct": gap_pct,
            "atr20x": atr20x,
            "volPct": volPct,
            "activity": activity,
            "h24": h24,
            "l24": l24,
            "quality": quality,
            "fresh": fresh,
            "staleSec": stale_sec,
            "ts": end_ms,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"features: {e}")


@app.get("/v1/price/tick")
async def v1_price_tick(symbol: str = "XAUUSD"):
    """
    Lightweight tick endpoint: returns bid/ask if available (TwelveData quote),
    otherwise synthesizes bid/ask around last. Includes freshness flag.
    """
    try:
        end_ms = now_utc_ms()
        bid = None
        ask = None
        last = None
        try:
            q = await fetch_twelvedata_quote(symbol)
            bid = q.get("bid")
            ask = q.get("ask")
            last = q.get("last")
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


@app.get("/v1/ohlc")
async def v1_ohlc(symbol: str = "XAUUSD", tf: str = "1m", limit: int = 1000):
    """
    Normalized OHLC fetcher. For now, uses get_candles() and slices the tail.
    tf is accepted for compatibility (1m/5m/1h), but current implementation
    returns the native interval of the provider path.
    """
    try:
        end_ms = now_utc_ms()
        candles, _last = await get_candles(symbol)
        # Resample if requested (server-side) to 5m or 1h
        bars = _resample_candles(candles, tf)
        if limit and limit > 0:
            bars = bars[-limit:]
        fresh, _stale = _staleness(bars[-1]["t"] if bars else 0, end_ms, fresh_threshold_min=5)
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


@app.get("/v1/levels/today")
async def v1_levels_today(symbol: str = "XAUUSD"):
    """
    UTC-based levels for today: DO (open at 00:00 UTC), and PDH/PDL from
    the previous UTC day. Computed from the current candles feed.
    """
    try:
        candles, _last = await get_candles(symbol)
        now_utc = datetime.now(timezone.utc)
        today_midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_midnight = today_midnight - timedelta(days=1)
        next_midnight = today_midnight + timedelta(days=1)

        today_window = filter_candles(candles, to_utc_ms(today_midnight), to_utc_ms(next_midnight))
        prev_window = filter_candles(candles, to_utc_ms(prev_midnight), to_utc_ms(today_midnight))

        do_price = compute_levels_for_window(today_window)["DO"]
        prev_levels = compute_levels_for_window(prev_window)
        return {
            "DO": do_price,
            "PDH": prev_levels["PDH"],
            "PDL": prev_levels["PDL"],
            "ts": now_utc_ms(),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"levels/today: {e}")


@app.get("/v1/calendar/upcoming")
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

@app.websocket("/ticks")
async def ws_ticks(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            ts_ms = now_utc_ms()
            bid = None
            ask = None
            last = None
            try:
                q = await fetch_twelvedata_quote("XAUUSD")
                bid = q.get("bid")
                ask = q.get("ask")
                last = q.get("last")
            except Exception:
                try:
                    _candles, last_p = await get_candles("XAUUSD")
                    last = last_p
                except Exception:
                    last = None
            if last is not None and (bid is None or ask is None):
                # synthesize a tiny spread if missing
                spread = max(0.05, 0.0005 * float(last))
                bid = float(last) - spread / 2.0
                ask = float(last) + spread / 2.0
            payload = {"ts": ts_ms}
            if bid is not None:
                payload["bid"] = float(bid)
            if ask is not None:
                payload["ask"] = float(ask)
            await ws.send_json(payload)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return


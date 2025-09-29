from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import time
import asyncio
import httpx
from datetime import datetime, timedelta, timezone
import pytz
from typing import Optional, Tuple, Dict, Any

NY = pytz.timezone("America/New_York")
YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
ALPHA_BASE = "https://www.alphavantage.co/query"
ALPHA_KEY = os.getenv("ALPHAVANTAGE_API_KEY")

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
    params = {"interval": "5m", "range": "2d"}
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


async def get_candles(symbol: str):
    key = ("candles", symbol.upper())
    ts_payload = _cache.get(key)
    if ts_payload and (now_utc_ms() - ts_payload[0] < 15_000):
        return ts_payload[1]
    try:
        payload = await fetch_yahoo(symbol)
    except Exception as e_yahoo:
        try:
            payload = await fetch_alpha(symbol)
        except Exception as e_alpha:
            raise RuntimeError(f"Yahoo failed: {e_yahoo}; Alpha failed: {e_alpha}")
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



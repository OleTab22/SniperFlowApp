import os, time, logging
import asyncio
import httpx
import re
from collections import defaultdict
import psycopg2, psycopg2.extras
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from typing import List, Dict

# Import the app router directly
from .app import router as data_router, startup as data_startup, shutdown as data_shutdown
try:
    # Base consolidated payload; we'll enrich calendar from DB below
    from .app import home as provider_home
except Exception:
    provider_home = None

# Third-party parsing libs for free official calendars
from icalendar import Calendar
from bs4 import BeautifulSoup
from dateutil import tz

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sniperflow")

# ---------- ENV ----------
DATABASE_URL = os.getenv("DATABASE_URL")
TD_KEY = os.getenv("TWELVEDATA_API_KEY")
FRED_KEY = os.getenv("FRED_API_KEY")
ENABLE_ML_COLLECTOR = (os.getenv("ENABLE_ML_COLLECTOR", "true").lower() == "true")

HEADERS = {"User-Agent": "sniperflow/1.0 (+contact)", "Accept": "application/json"}
_CACHE: dict[str, tuple[float, object]] = {}
_LOCKS = defaultdict(asyncio.Lock)
SAST = tz.gettz("Africa/Johannesburg")

async def _get_json(url: str, params: dict | None = None, timeout=12):
    async with httpx.AsyncClient(timeout=timeout, headers=HEADERS) as s:
        r = await s.get(url, params=params)
        if r.status_code == 429:
            # surface as a retriable error; our backoff will catch it
            raise httpx.HTTPStatusError("rate limit", request=r.request, response=r)
        r.raise_for_status()
        return r.json()

async def _with_backoff(coro_factory, tries=3, base=0.6):
    for i in range(tries):
        try:
            return await coro_factory()
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout):
            await asyncio.sleep(base * (2 ** i))
    # last attempt (let exception bubble if fails)
    return await coro_factory()

async def _cached(key: str, ttl_sec: int, fetcher):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    async with _LOCKS[key]:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
        val = await fetcher()
        _CACHE[key] = (now + ttl_sec, val)
        return val

# ------------------ Providers (no Yahoo) ------------------

async def td_quote_pct(symbol: str) -> float | None:
    """Return percent change for symbol from Twelve Data, e.g. DXY, SPY, XAU/USD."""
    if not TD_KEY:
        return None
    url = "https://api.twelvedata.com/quote"
    def _parse(j):
        # TD may return strings like "0.45" or "0.45%"; normalize
        pct = str(j.get("percent_change", "0")).replace("%", "")
        try:
            return float(pct)
        except Exception:
            return None
    async def fetch():
        j = await _get_json(url, {"symbol": symbol, "apikey": TD_KEY})
        if isinstance(j, dict) and j.get("status") == "error":
            return None
        return _parse(j)
    return await _with_backoff(lambda: fetch())

async def td_series_xau_5m() -> dict | None:
    """Last ~60 x 5m candles for XAU/USD (kept small for quotas)."""
    if not TD_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    async def fetch():
        return await _get_json(url, {
            "symbol": "XAU/USD",
            "interval": "5min",
            "outputsize": 60,      # last ~5 hours, not 390!
            "apikey": TD_KEY,
            "timezone": "UTC",
            "order": "ASC",
            "format": "JSON"
        })
    return await _with_backoff(lambda: fetch())

async def fred_us10y_delta() -> float | None:
    """Latest daily delta for 10Y yield (DGS10)."""
    if not FRED_KEY:
        return None
    url = "https://api.stlouisfed.org/fred/series/observations"
    async def fetch():
        j = await _get_json(url, {
            "series_id": "DGS10",
            "api_key": FRED_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 2
        }, timeout=20)
        obs = [o for o in j.get("observations", []) if o.get("value") not in (".", None)]
        if not obs:
            return None
        if len(obs) == 1:
            return float(obs[0]["value"])
        return float(obs[0]["value"]) - float(obs[1]["value"])
    return await _with_backoff(lambda: fetch(), tries=2, base=1.2)

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except Exception:
        return psycopg2.connect(DATABASE_URL)

# ---------------- Free, official calendar providers ----------------

async def _get_text(url: str) -> str:
    # Use a browser-like UA and accept headers; some official sites block generic bots
    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    headers = dict(base_headers)
    if url.endswith(".ics"):
        headers.update({
            "Accept": "text/calendar, text/plain; q=0.9, */*; q=0.8",
            "Referer": "https://www.bls.gov/",
        })
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as cx:
        r = await cx.get(url) # follow_redirects is already set in the client constructor
        r.raise_for_status()
        return r.text

def _norm_impact(s: str | None) -> str:
    lbl = (s or "").strip().title()
    return lbl if lbl in ("Low", "Medium", "High") else "High"

def _mk_event(title: str, dt_aware: datetime, country: str, source: str,
              url: str | None = None, category: str | None = None,
              currency: str | None = None, importance: int = 3) -> Dict:
    return {
        "title": title,
        "time": dt_aware.astimezone(timezone.utc),
        "impact": _norm_impact("High" if importance == 3 else "Medium"),
        "country": country,
        "currency": currency,
        "category": category,
        "source": source,
        "url": url,
        "importance": importance,
    }

# BLS iCal
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"

async def fetch_bls_ics(start_utc: datetime, end_utc: datetime) -> List[Dict]:
    ics = await _get_text(BLS_ICS)
    cal = Calendar.from_ical(ics)
    out: List[Dict] = []
    want = (
        "Consumer Price Index",
        "Employment Situation",
        "Producer Price Indexes",
        "Import and Export Price Indexes",
        "Real Earnings",
        "Job Openings and Labor Turnover Survey",
    )
    for comp in cal.walk():
        if getattr(comp, 'name', '') != "VEVENT":
            continue
        summary = str(comp.get("SUMMARY") or "")
        if not any(w in summary for w in want):
            continue
        dtstart = comp.decoded("DTSTART")
        if not isinstance(dtstart, datetime):
            continue
        dt_utc = dtstart.astimezone(timezone.utc)
        if not (start_utc <= dt_utc <= end_utc):
            continue
        url = str(comp.get("URL") or "") or "https://www.bls.gov/schedule/"
        out.append(_mk_event(
            title=f"US {summary}", dt_aware=dtstart, country="US", source="BLS (ICS)",
            url=url, category=summary, currency="USD", importance=3
        ))
    return out

# BEA schedule
BEA_SCHEDULE = "https://www.bea.gov/news/schedule"
_time_rx = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)", re.I)

async def fetch_bea(start_utc: datetime, end_utc: datetime) -> List[Dict]:
    html = await _get_text(BEA_SCHEDULE)
    soup = BeautifulSoup(html, "lxml")
    out: List[Dict] = []
    for li in soup.select("div.view-content div.views-row, li.views-row, div.schedule-list div.row"):
        text = " ".join(li.get_text(" ", strip=True).split())
        if not text:
            continue
        date_match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+202\d", text)
        if not date_match:
            continue
        date_str = date_match.group(0)
        time_match = _time_rx.search(text)
        time_str = time_match.group(0) if time_match else "8:30 AM"
        title = text.split("  ")[0]
        dt_naive = datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M %p")
        dt_ny = dt_naive.replace(tzinfo=tz.gettz("America/New_York"))
        dt_utc = dt_ny.astimezone(timezone.utc)
        if start_utc <= dt_utc <= end_utc:
            out.append(_mk_event(
                title=f"US {title}", dt_aware=dt_ny, country="US", source="BEA",
                url=BEA_SCHEDULE, category=title, currency="USD", importance=3
            ))
    return out

# Federal Reserve (FOMC)
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

async def fetch_fomc(start_utc: datetime, end_utc: datetime) -> List[Dict]:
    html = await _get_text(FOMC_URL)
    soup = BeautifulSoup(html, "lxml")
    out: List[Dict] = []
    for li in soup.select("div#article li, table tr"):
        t = " ".join(li.get_text(" ", strip=True).split())
        if not t:
            continue
        if ("Press Conference" in t) or ("Statement" in t) or ("FOMC Meeting" in t):
            date_match = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+202\d", t)
            if not date_match:
                continue
            date_str = date_match.group(0)
            tm = re.search(r"(\d{1,2}:\d{2})\s*p\.m\.|(\d{1,2}:\d{2})\s*a\.m\.", t, re.I)
            time_str = tm.group(0).replace(".", "").upper() if tm else "2:00 PM"
            time_clean = (time_str
                          .replace(" P M", " PM").replace(" A M", " AM")
                          .replace("P M", " PM").replace("A M", " AM")
                          .replace("P.M", "PM").replace("A.M", "AM"))
            if ":" not in time_clean:
                time_clean = "2:00 PM"
            dt_naive = datetime.strptime(f"{date_str} {time_clean}", "%B %d, %Y %I:%M %p")
            dt_ny = dt_naive.replace(tzinfo=tz.gettz("America/New_York"))
            dt_utc = dt_ny.astimezone(timezone.utc)
            if start_utc <= dt_utc <= end_utc:
                out.append(_mk_event(
                    title=f"US FOMC {'Press Conference' if 'Press Conference' in t else 'Statement/Meeting'}",
                    dt_aware=dt_ny, country="US", source="Federal Reserve",
                    url=FOMC_URL, category="FOMC", currency="USD", importance=3
                ))
    return out

# ECB
ECB_URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"

async def fetch_ecb(start_utc: datetime, end_utc: datetime) -> List[Dict]:
    html = await _get_text(ECB_URL)
    soup = BeautifulSoup(html, "lxml")
    out: List[Dict] = []
    for li in soup.select("main li"):
        t = " ".join(li.get_text(" ", strip=True).split())
        if ("Press conference" in t) or ("monetary policy meeting" in t.lower()):
            m = re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
            if not m:
                continue
            d, mth, y = m.groups()
            tm = re.search(r"(\d{1,2}:\d{2})", t)
            time_str = tm.group(1) if tm else "14:45"
            dt_naive = datetime.strptime(f"{y}-{mth}-{d} {time_str}", "%Y-%m-%d %H:%M")
            dt_cet = dt_naive.replace(tzinfo=tz.gettz("Europe/Brussels"))
            dt_utc = dt_cet.astimezone(timezone.utc)
            if start_utc <= dt_utc <= end_utc:
                out.append(_mk_event(
                    title="ECB Press Conference",
                    dt_aware=dt_cet, country="EU", source="ECB",
                    url=ECB_URL, category="ECB Governing Council", currency="EUR", importance=3
                ))
    return out

# Optional FRED release dates (uses free key if provided)
FRED_RELEASES = "https://api.stlouisfed.org/fred/releases/dates"

async def fetch_fred_dates(start_utc: datetime, end_utc: datetime) -> List[Dict]:
    if not FRED_KEY:
        return []
    params = {
        "api_key": FRED_KEY,
        "file_type": "json",
        "order_by": "release_date",
        "sort_order": "asc",
        "realtime_start": start_utc.date().isoformat(),
        "realtime_end": end_utc.date().isoformat(),
        "include_release_dates_with_no_data": "true",
    }
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.get(FRED_RELEASES, params=params)
        r.raise_for_status()
        j = r.json()
    out: List[Dict] = []
    # Filter to only relevant macro releases (exclude crypto, state-level, industry-specific)
    RELEVANT = (
        "Consumer Price Index",
        "Employment Situation",
        "Producer Price",
        "Gross Domestic Product",
        "Personal Income",
        "Advance Economic Indicators",
        "Industrial Production",
        "Retail Sales",
        "Durable Goods",
        "Housing Starts",
        "Trade",
        "Federal Open Market Committee",
        "Productivity and Costs",
        "Import and Export",
        "PCE",
    )
    for rd in j.get("release_dates", []):
        name = rd.get("release_name", "")
        # Skip crypto, state-level, and irrelevant releases
        if not any(rel.lower() in name.lower() for rel in RELEVANT):
            continue
        date_str = rd.get("date")
        if not date_str:
            continue
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt_ny = dt.replace(hour=8, minute=30, tzinfo=tz.gettz("America/New_York"))
        dt_utc = dt_ny.astimezone(timezone.utc)
        if start_utc <= dt_utc <= end_utc:
            out.append(_mk_event(
                title=f"US {name}", dt_aware=dt_ny, country="US",
                source="FRED (release date)", url="https://fred.stlouisfed.org/releases/calendar",
                category=name, currency="USD", importance=2
            ))
    return out

def _upsert_calendar_rows(items: List[Dict]):
    if not items or not DATABASE_URL:
        return
    with connect() as c, c.cursor() as cur:
        for it in items:
            cur.execute(
                """
                INSERT INTO calendar (title,time,impact,country,currency,category,actual,forecast,previous,source,url,importance)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (time, title, country) DO UPDATE SET
                  impact=EXCLUDED.impact,
                  category=EXCLUDED.category,
                  actual=EXCLUDED.actual,
                  forecast=EXCLUDED.forecast,
                  previous=EXCLUDED.previous,
                  source=EXCLUDED.source,
                  url=EXCLUDED.url,
                  importance=EXCLUDED.importance
                """,
                (
                    it.get("title"), it.get("time"), it.get("impact"), it.get("country"),
                    it.get("currency"), it.get("category"), it.get("actual"), it.get("forecast"),
                    it.get("previous"), it.get("source"), it.get("url"), it.get("importance"),
                )
            )
        c.commit()

async def sync_calendar_free():
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=1)
    end = now + timedelta(days=14)
    tasks = [
        fetch_bls_ics(start, end),
        fetch_bea(start, end),
        fetch_fomc(start, end),
        fetch_ecb(start, end),
        fetch_fred_dates(start, end),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    items: List[Dict] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("calendar provider failed: %s", r)
            continue
        items.extend(r)
    # de-dup per minute bucket
    seen: set[tuple] = set()
    deduped: List[Dict] = []
    for e in sorted(items, key=lambda x: x["time"]):
        key = (e.get("title"), e.get("country"), e.get("time").replace(second=0, microsecond=0))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    _upsert_calendar_rows(deduped)

def start_calendar_sync_background():
    async def runner():
        while True:
            try:
                await sync_calendar_free()
            except Exception as e:
                log.warning("calendar sync failed: %s", e)
            await asyncio.sleep(15 * 60)
    asyncio.create_task(runner())

# ---------- APP ----------
app = FastAPI(title="SniperFlow API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=True)

# (route registration moved to decorator below)

# Include non-DB endpoints from backend.app (market/levels/etc.) so one server serves all
if data_router is not None:
    app.include_router(data_router)
    log.info("Mounted data app router; total routes: %d", len(app.routes))

# Override /home to enrich calendar from DB using official sources (keeps provider payload intact)
@app.get("/home")
async def home(nocache: bool = False):
    base = {}
    if callable(provider_home):
        try:
            base = await provider_home(nocache=nocache)  # type: ignore
        except Exception as e:
            log.warning("provider home failed: %s", e)
            base = {}
    if not DATABASE_URL:
        return base or {"calendar": None}
    # Pick the next upcoming event within 168 hours; prefer importance=3
    next_evt = None
    with connect() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT title,time,impact,country,currency,category,source,url,importance
            FROM calendar
            WHERE time BETWEEN now() AND now() + interval '168 hour'
            ORDER BY COALESCE(importance,0) DESC, time ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            t_ms = int(row[1].timestamp() * 1000)
            lock_start = int(((row[1] - timedelta(minutes=15)).timestamp()))
            lock_end = int(((row[1] + timedelta(minutes=15)).timestamp()))
            next_evt = {
                "title": str(row[0] or ""),
                "impact": (row[2] or "High"),
                "time_utc": str(int(t_ms // 1000)),
                "lock_window": {"start_utc": str(lock_start), "end_utc": str(lock_end)},
            }
    if next_evt:
        try:
            base = dict(base) if isinstance(base, dict) else {}
            base["calendar"] = {"next_red": next_evt}
        except Exception:
            pass
    return base

@app.post("/v1/calendar/sync-now")
async def v1_calendar_sync_now():
    try:
        await sync_calendar_free()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"calendar sync failed: {e}")

# WebSocket endpoint for /ticks — mirror router JSON payload {ts,bid,ask}
@app.websocket("/ticks")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                # Import provider helpers lazily to avoid circular imports at module load
                from .app import cached_twelvedata_quote, get_candles, now_utc_ms
            except Exception:
                cached_twelvedata_quote = None
                get_candles = None
                def now_utc_ms():
                    return int(time.time() * 1000)

            ts_ms = now_utc_ms() if callable(now_utc_ms) else int(time.time() * 1000)
            bid = None
            ask = None
            last = None
            # Try TwelveData quote first if available
            try:
                if cached_twelvedata_quote is not None:
                    q = await cached_twelvedata_quote("XAUUSD")
                    bid = q.get("bid")
                    ask = q.get("ask")
                    last = q.get("last")
            except Exception:
                pass
            # Fallback to last price from candles
            if last is None:
                try:
                    if get_candles is not None:
                        _candles, last_p = await get_candles("XAUUSD")
                        last = last_p
                except Exception:
                    last = None
            # Synthesize a small spread if only last is known
            if last is not None and (bid is None or ask is None):
                spread = max(0.05, 0.0005 * float(last))
                bid = float(last) - spread / 2.0
                ask = float(last) + spread / 2.0

            payload = {"ts": ts_ms}
            if bid is not None:
                payload["bid"] = float(bid)
            if ask is not None:
                payload["ask"] = float(ask)
            await websocket.send_json(payload)
            await asyncio.sleep(1.0)
    except Exception as e:
        log.warning(f"WebSocket Error: {e}")
    finally:
        await websocket.close()
        log.info("WebSocket connection closed")


# Lightweight health path for clients expecting /health
@app.get("/health")
def health_root():
    return {"status": "ok"}

def migrate_once():
    """
    Durable, idempotent migration guarded by a Postgres advisory lock.
    Creates tables and indexes if not present and seeds minimal rows when empty.
    """
    with connect() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Acquire advisory lock to ensure only 1 instance migrates
            cur.execute("SELECT pg_try_advisory_lock(%s)", (8675309,))
            locked = cur.fetchone()[0]
            if not locked:
                log.info("Migration already in progress elsewhere; skipping.")
                return

            log.info("Running startup migration…")

            # Schema
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS levels(
                  id SERIAL PRIMARY KEY,
                  date DATE NOT NULL UNIQUE,
                  do_price DOUBLE PRECISION,
                  pdh DOUBLE PRECISION,
                  pdl DOUBLE PRECISION
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS nowcast(
                  id SERIAL PRIMARY KEY,
                  ts TIMESTAMPTZ DEFAULT now(),
                  score INTEGER,
                  drivers JSONB
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar(
                  id SERIAL PRIMARY KEY,
                  title TEXT,
                  time TIMESTAMPTZ,
                  impact TEXT
                );
                """
            )
            # Calendar optional columns for richer metadata (idempotent)
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS country TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS currency TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS category TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS actual TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS forecast TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS previous TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS source TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS url TEXT;")
            cur.execute("ALTER TABLE calendar ADD COLUMN IF NOT EXISTS importance INTEGER;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS journal(
                  id SERIAL PRIMARY KEY,
                  user_id TEXT,
                  alert_id TEXT,
                  notes TEXT,
                  timestamp TIMESTAMPTZ DEFAULT now()
                );
                """
            )

            # Expand journal schema with additional columns (idempotent)
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS direction TEXT;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS timeframe TEXT;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS entry DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS sl DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS tp DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS planned_rr DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS realized_rr DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS session TEXT;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS bias TEXT;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS do_lvl DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS pdh DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS pdl DOUBLE PRECISION;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS tags TEXT;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS client_local_id INTEGER;")
            cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS client_created_at TIMESTAMPTZ;")

            # Indexes (safe to repeat)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calendar_time ON calendar(time);")
            # Upsert key for (time,title,country)
            cur.execute(
                """
                DO $$ BEGIN
                IF NOT EXISTS (
                  SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname='calendar_unique_idx'
                ) THEN
                  CREATE UNIQUE INDEX calendar_unique_idx ON calendar(time, COALESCE(title,''), COALESCE(country,''));
                END IF;
                END $$;
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(timestamp DESC);")
            # Support upsert via (user_id, client_local_id) to avoid cross-user collisions
            # Create a unique index first (version-safe), then attach it as a constraint if missing
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS journal_user_client_idx ON journal(user_id, client_local_id);")
            cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'uq_journal_user_client'")
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE journal ADD CONSTRAINT uq_journal_user_client UNIQUE USING INDEX journal_user_client_idx;")
            # Older deployments may have a partial unique index on client_local_id; harmless to keep for lookups
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_journal_client_local ON journal(client_local_id) WHERE client_local_id IS NOT NULL;")

            # Seed minimal data only if empty
            cur.execute("SELECT 1 FROM levels WHERE date = CURRENT_DATE LIMIT 1;")
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO levels(date,do_price,pdh,pdl)
                    VALUES (CURRENT_DATE,1840.50,1851.20,1832.00)
                    ON CONFLICT (date) DO NOTHING;
                    """
                )

            # Seed stubs removed — real data comes from free official sources via sync_calendar_free()
            # No need to seed stub calendar events; the background sync will populate real events

            conn.commit()
            log.info("Migration complete.")

@app.on_event("startup")
async def startup_event():
    # Initialize the data app client
    await data_startup()
    # DB migration logic
    if not DATABASE_URL:
        return
    for i in range(5):
        try:
            migrate_once()
            break
        except Exception as e:
            wait = 2 ** i
            log.warning("Migration attempt %s failed: %s (retrying in %ss)", i+1, e, wait)
            await asyncio.sleep(wait)
    
    # Start ML data collector in background (guarded by env)
    if ENABLE_ML_COLLECTOR:
        try:
            from .ml_collector import start_background_collector
            start_background_collector()
            log.info("ML data collector started")
        except Exception as e:
            log.warning(f"ML collector not started: {e}")
    else:
        log.info("ML data collector disabled by ENABLE_ML_COLLECTOR=false")
    # Start free official calendar sync
    try:
        start_calendar_sync_background()
        log.info("Calendar sync started")
    except Exception as e:
        log.warning(f"Calendar sync not started: {e}")
    # Kick one immediate sync so the UI has real events on first load
    try:
        await sync_calendar_free()
        log.info("Calendar sync initial pass complete")
    except Exception as e:
        log.warning(f"Initial calendar sync failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    await data_shutdown()
    # Best-effort stop of ML collector if running
    try:
        from .ml_collector import stop_background_collector
        stop_background_collector()
    except Exception:
        pass

# ---------- MODELS ----------
class JournalIn(BaseModel):
    user_id: str
    alert_id: str
    notes: str
    direction: str | None = None
    timeframe: str | None = None
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    planned_rr: float | None = None
    realized_rr: float | None = None
    session: str | None = None
    bias: str | None = None
    doLvl: float | None = None
    pdh: float | None = None
    pdl: float | None = None
    tags: list[str] | None = None
    client_id: int | None = None
    created_at_ms: int | None = None

# ---------- ROUTES ----------
@app.get("/v1/health")
def health():
    # confirm schema exists (permanent health signal)
    with connect() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT to_regclass('public.levels') IS NOT NULL,
                   to_regclass('public.calendar') IS NOT NULL,
                   to_regclass('public.nowcast') IS NOT NULL,
                   to_regclass('public.journal') IS NOT NULL;
            """
        )
        lvl, cal, nwc, jrn = cur.fetchone()
    return {
        "ok": True,
        "time": datetime.utcnow().isoformat(),
        "schema": {"levels": lvl, "calendar": cal, "nowcast": nwc, "journal": jrn}
    }

@app.get("/v1/levels/today")
async def levels_today(symbol: str = "XAUUSD"):
    """
    Align response with Android client: {DO, PDH, PDL, ts}.
    Prefer DB values when available; otherwise delegate to provider-based logic.
    """
    # Try DB first if configured
    if DATABASE_URL:
        try:
            with connect() as c, c.cursor() as cur:
                cur.execute("SELECT do_price, pdh, pdl FROM levels WHERE date = CURRENT_DATE")
                row = cur.fetchone()
                if row:
                    do_price, pdh, pdl = row
                    return {"DO": do_price, "PDH": pdh, "PDL": pdl, "ts": int(time.time() * 1000)}
        except Exception:
            # fall through to provider path
            pass
    # Delegate to router provider implementation for consistency
    try:
        from .app import v1_levels_today as provider_levels_today
        return await provider_levels_today(symbol)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"levels/today: {e}")

@app.get("/v1/calendar/upcoming")
def calendar_upcoming(window: str = "8h"):
    hrs = int(window.rstrip("hH"))
    if not DATABASE_URL:
        # fallback empty structure when DB is not configured
        return {"items": []}
    with connect() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT title, EXTRACT(EPOCH FROM time)::bigint, COALESCE(impact,'High')
            FROM calendar WHERE time BETWEEN now() AND now() + interval %s
            ORDER BY COALESCE(importance,0) DESC, time ASC
            LIMIT 1
            """, (f"{hrs} hour",))
        row = cur.fetchone()
        if row:
            t_ms = int(row[1] * 1000)
            lock_start = int(((row[1] - timedelta(minutes=15)).timestamp()))
            lock_end = int(((row[1] + timedelta(minutes=15)).timestamp()))
            next_evt = {
                "title": str(row[0] or ""),
                "impact": (row[2] or "High"),
                "time_utc": str(t_ms),
                "lock_window": {"start_utc": str(lock_start), "end_utc": str(lock_end)},
            }
    if next_evt:
        try:
            base = {}
            base["calendar"] = {"next_red": next_evt}
        except Exception:
            pass
    return base

def _score_from(drivers: list[dict]) -> int:
    # DXY and US10Y up -> bearish (-); SPY up -> bullish (+)
    m = {d["id"]: d for d in drivers}
    s = (-float(m.get("DXY", {}).get("z", 0.0))
         -float(m.get("US10Y", {}).get("z", 0.0))
         +float(m.get("SPY", {}).get("z", 0.0)))
    return max(-100, min(100, int(round(s * 20))))

@app.get("/v1/nowcast")
async def nowcast():
    async def build():
        # cache each provider to keep well below quotas
        dxy = await _cached("pct:DXY", 120, lambda: td_quote_pct("DXY"))
        spy = await _cached("pct:SPY", 120, lambda: td_quote_pct("SPY"))
        us10y = await _cached("delta:DGS10", 6 * 3600, fred_us10y_delta)

        drivers = []
        if dxy is not None:  drivers.append({"id": "DXY",  "z": dxy,   "w": 0.34, "fresh": True, "staleSec": 0})
        if us10y is not None:drivers.append({"id": "US10Y","z": us10y, "w": 0.33, "fresh": True, "staleSec": 0})
        if spy is not None:  drivers.append({"id": "SPY",  "z": spy,   "w": 0.33, "fresh": True, "staleSec": 0})

        if not drivers:
            return {"score": 0, "drivers":[
                {"id":"DXY","z":0,"w":0.34,"fresh":False},
                {"id":"US10Y","z":0,"w":0.33,"fresh":False},
                {"id":"SPY","z":0,"w":0.33,"fresh":False},
            ]}
        return {"score": _score_from(drivers), "drivers": drivers}

    # cache the whole payload as well (2 minutes) and attach ts like v1 in app router
    resp = await _cached("nowcast", 120, build)
    try:
        if isinstance(resp, dict) and "ts" not in resp:
            resp = {**resp, "ts": int(time.time() * 1000)}
    except Exception:
        pass
    return resp

@app.get("/v1/xau/series5m")
async def xau_series5m():
    data = await _cached("series:XAU5", 60, td_series_xau_5m)
    return data or {"status": "error", "message": "XAU series unavailable"}

@app.post("/v1/journal", status_code=201)
def post_journal(entry: JournalIn):
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor() as cur:
        tags_csv = ",".join(entry.tags) if entry.tags else None
        client_created_at = None
        if entry.created_at_ms is not None:
            try:
                client_created_at = datetime.fromtimestamp(int(entry.created_at_ms) / 1000.0, tz=timezone.utc)
            except Exception:
                client_created_at = None
        # Simple upsert by client_local_id when provided
        if entry.client_id is not None:
            cur.execute(
                """
                INSERT INTO journal (
                    user_id, alert_id, notes,
                    direction, timeframe, entry, sl, tp,
                    planned_rr, realized_rr, session, bias,
                    do_lvl, pdh, pdl, tags,
                    client_local_id, client_created_at
                ) VALUES (
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s
                )
                ON CONFLICT (user_id, client_local_id) DO UPDATE SET
                    user_id=EXCLUDED.user_id,
                    alert_id=EXCLUDED.alert_id,
                    notes=EXCLUDED.notes,
                    direction=EXCLUDED.direction,
                    timeframe=EXCLUDED.timeframe,
                    entry=EXCLUDED.entry,
                    sl=EXCLUDED.sl,
                    tp=EXCLUDED.tp,
                    planned_rr=EXCLUDED.planned_rr,
                    realized_rr=EXCLUDED.realized_rr,
                    session=EXCLUDED.session,
                    bias=EXCLUDED.bias,
                    do_lvl=EXCLUDED.do_lvl,
                    pdh=EXCLUDED.pdh,
                    pdl=EXCLUDED.pdl,
                    tags=EXCLUDED.tags,
                    client_created_at=EXCLUDED.client_created_at
                RETURNING id
                """,
                (
                    entry.user_id, entry.alert_id, entry.notes,
                    entry.direction, entry.timeframe, entry.entry, entry.sl, entry.tp,
                    entry.planned_rr, entry.realized_rr, entry.session, entry.bias,
                    entry.doLvl, entry.pdh, entry.pdl, tags_csv,
                    entry.client_id, client_created_at,
                )
            )
        else:
            cur.execute(
                """
                INSERT INTO journal (
                    user_id, alert_id, notes,
                    direction, timeframe, entry, sl, tp,
                    planned_rr, realized_rr, session, bias,
                    do_lvl, pdh, pdl, tags,
                    client_created_at
                ) VALUES (
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s
                ) RETURNING id
                """,
                (
                    entry.user_id, entry.alert_id, entry.notes,
                    entry.direction, entry.timeframe, entry.entry, entry.sl, entry.tp,
                    entry.planned_rr, entry.realized_rr, entry.session, entry.bias,
                    entry.doLvl, entry.pdh, entry.pdl, tags_csv,
                    client_created_at,
                )
            )
        jid = cur.fetchone()[0]
        c.commit()
        return {"id": jid}

# tiny debug helper (optional)
@app.get("/v1/journal/latest")
def latest_journal():
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("SELECT * FROM journal ORDER BY timestamp DESC LIMIT 1")
        row = cur.fetchone()
        return dict(row) if row else {}

@app.put("/v1/journal/{jid}")
def put_journal(jid: int, entry: JournalIn):
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor() as cur:
        tags_csv = ",".join(entry.tags) if entry.tags else None
        client_created_at = None
        if entry.created_at_ms is not None:
            try:
                client_created_at = datetime.fromtimestamp(int(entry.created_at_ms) / 1000.0, tz=timezone.utc)
            except Exception:
                client_created_at = None
        cur.execute(
            """
            UPDATE journal SET
                user_id=%s, alert_id=%s, notes=%s,
                direction=%s, timeframe=%s, entry=%s, sl=%s, tp=%s,
                planned_rr=%s, realized_rr=%s, session=%s, bias=%s,
                do_lvl=%s, pdh=%s, pdl=%s, tags=%s,
                client_local_id=%s, client_created_at=%s
            WHERE id=%s RETURNING id
            """,
            (
                entry.user_id, entry.alert_id, entry.notes,
                entry.direction, entry.timeframe, entry.entry, entry.sl, entry.tp,
                entry.planned_rr, entry.realized_rr, entry.session, entry.bias,
                entry.doLvl, entry.pdh, entry.pdl, tags_csv,
                entry.client_id, client_created_at,
                jid,
            )
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "journal not found")
        c.commit()
        return {"id": row[0]}

@app.delete("/v1/journal/{jid}")
def delete_journal(jid: int):
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor() as cur:
        cur.execute("DELETE FROM journal WHERE id=%s", (jid,))
        c.commit()
        return {"ok": True}

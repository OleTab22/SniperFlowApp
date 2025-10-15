import os, time, logging
import asyncio
import httpx
from collections import defaultdict
import psycopg2, psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sniperflow")

# ---------- ENV ----------
DATABASE_URL = os.getenv("DATABASE_URL")
TD_KEY = os.getenv("TWELVEDATA_API_KEY")
FRED_KEY = os.getenv("FRED_API_KEY")

HEADERS = {"User-Agent": "sniperflow/1.0 (+contact)", "Accept": "application/json"}
_CACHE: dict[str, tuple[float, object]] = {}
_LOCKS = defaultdict(asyncio.Lock)

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

# ---------- APP ----------
app = FastAPI(title="SniperFlow API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Include non-DB endpoints from backend.app (market/levels/home etc.) so one server serves all
try:
    from .app import app as data_app
    app.include_router(data_app.router)
except Exception:
    # If the import fails in certain environments, continue with DB-only API
    pass

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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(timestamp DESC);")
            # Support upsert via client_local_id
            # Use a proper UNIQUE constraint so Postgres can match ON CONFLICT reliably.
            # (Partial unique indexes are not considered by ON CONFLICT without inference conditions.)
            cur.execute("ALTER TABLE journal ADD CONSTRAINT IF NOT EXISTS uq_journal_client_local UNIQUE (client_local_id);")
            # Older deployments may still have a partial unique index; harmless to keep, but safe to (re)create if missing
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

            cur.execute("SELECT 1 FROM calendar LIMIT 1;")
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO calendar(title,time,impact) VALUES
                    ('US PMI', now() + interval '2 hour','High'),
                    ('FOMC Minutes', now() + interval '6 hour','High');
                    """
                )

            conn.commit()
            log.info("Migration complete.")

@app.on_event("startup")
def _startup():
    # backoff to survive cold boots / transient DB readiness
    if not DATABASE_URL:
        return
    for i in range(5):
        try:
            migrate_once()
            break
        except Exception as e:
            wait = 2 ** i
            log.warning("Migration attempt %s failed: %s (retrying in %ss)", i+1, e, wait)
            time.sleep(wait)

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
def levels_today():
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT date, do_price, pdh, pdl FROM levels WHERE date = CURRENT_DATE")
        row = cur.fetchone()
        if not row: raise HTTPException(404, "No levels for today")
        d, do_price, pdh, pdl = row
        return {"date": str(d), "do": do_price, "pdh": pdh, "pdl": pdl}

@app.get("/v1/calendar/upcoming")
def calendar_upcoming(window: str = "8h"):
    hrs = int(window.rstrip("hH"))
    if not DATABASE_URL:
        # fallback empty structure when DB is not configured
        return {"items": []}
    with connect() as c, c.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""SELECT title,time,impact FROM calendar
                       WHERE time BETWEEN now() AND now() + interval %s
                       ORDER BY time ASC""", (f"{hrs} hour",))
        return {"items": [dict(r) for r in cur.fetchall()]}

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

    # cache the whole payload as well (2 minutes)
    return await _cached("nowcast", 120, build)

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
                ON CONFLICT (client_local_id) DO UPDATE SET
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



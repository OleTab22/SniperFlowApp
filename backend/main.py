import os, time, logging
import psycopg2, psycopg2.extras
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sniperflow")

# ---------- ENV ----------
DATABASE_URL = os.getenv("DATABASE_URL")

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
                  do  DOUBLE PRECISION,
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

            # Indexes (safe to repeat)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_calendar_time ON calendar(time);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(timestamp DESC);")

            # Seed minimal data only if empty
            cur.execute("SELECT 1 FROM levels WHERE date = CURRENT_DATE LIMIT 1;")
            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO levels(date,do,pdh,pdl)
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
        cur.execute("SELECT date,do,pdh,pdl FROM levels WHERE date = CURRENT_DATE")
        row = cur.fetchone()
        if not row: raise HTTPException(404, "No levels for today")
        d, do, pdh, pdl = row
        return {"date": str(d), "do": do, "pdh": pdh, "pdl": pdl}

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

@app.get("/v1/nowcast")
def nowcast():
    if DATABASE_URL:
        with connect() as c, c.cursor() as cur:
            cur.execute("SELECT score,drivers FROM nowcast ORDER BY ts DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                s, drivers = row
                return {"score": int(s), "drivers": drivers}
    return {"score": 0, "drivers":[
        {"id":"DXY","z":0,"w":0.33,"fresh":False},
        {"id":"SPX","z":0,"w":0.33,"fresh":False},
        {"id":"ATR","z":0,"w":0.34,"fresh":False}
    ]}

@app.post("/v1/journal", status_code=201)
def post_journal(entry: JournalIn):
    if not DATABASE_URL:
        raise HTTPException(503, "DB not configured")
    with connect() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO journal (user_id, alert_id, notes)
                       VALUES (%s,%s,%s) RETURNING id""",
                    (entry.user_id, entry.alert_id, entry.notes))
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



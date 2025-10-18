"""
Background ML data collector for Render deployment.
Runs continuously, collecting features every 5 minutes and storing to PostgreSQL.
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("ml_collector")

# Import postgres connection from main
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

DATABASE_URL = os.getenv("DATABASE_URL")

def connect():
    """Connect to Postgres (same as main.py)."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except Exception:
        return psycopg2.connect(DATABASE_URL)

def ensure_ml_tables():
    """Create ml_features table if not exists (idempotent)."""
    if not DATABASE_URL:
        return
    
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ml_features (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    price_now DOUBLE PRECISION,
                    dxy_z DOUBLE PRECISION,
                    real_z DOUBLE PRECISION,
                    vix_z DOUBLE PRECISION,
                    risk_z DOUBLE PRECISION,
                    nom_z DOUBLE PRECISION,
                    do_ctx DOUBLE PRECISION,
                    mom DOUBLE PRECISION,
                    range_to_atr20 DOUBLE PRECISION,
                    activity DOUBLE PRECISION,
                    vol_pct INTEGER,
                    spread_pts INTEGER,
                    news_lock BOOLEAN,
                    gap_pct DOUBLE PRECISION,
                    pct24h DOUBLE PRECISION,
                    sess_asia BOOLEAN,
                    sess_london BOOLEAN,
                    sess_newyork BOOLEAN,
                    sess_off BOOLEAN,
                    q_ok BOOLEAN,
                    q_degraded BOOLEAN,
                    q_poor BOOLEAN,
                    dxyz_fresh BOOLEAN,
                    realz_fresh BOOLEAN,
                    vixz_fresh BOOLEAN,
                    risk_on_fresh BOOLEAN,
                    nominalz_fresh BOOLEAN,
                    do_ctx_fresh BOOLEAN,
                    mom_fresh BOOLEAN
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ml_features_ts ON ml_features(ts DESC);")
            conn.commit()
            log.info("ml_features table ready")

async def collect_one_sample():
    """Fetch /home and save features to DB."""
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set, skipping collection")
        return
    
    try:
        # Import here to avoid circular dependency
        from .app import home as home_handler
        
        # Call /home internally
        home_data = await home_handler(nocache=True)
        
        if not home_data:
            log.warning("home() returned empty")
            return
        
        # Extract features (same logic as _build_ml_features_from_home_payload)
        price = home_data.get("price", {})
        metrics = home_data.get("metrics", {})
        quality = home_data.get("quality", {})
        sessions = home_data.get("sessions", {})
        gates = home_data.get("gates", {})
        nowcast_m = metrics.get("nowcast", {}) or {}
        drivers = nowcast_m.get("drivers", []) or []
        
        def get_driver(key, default=0.0):
            for d in drivers:
                if d.get("key") == key:
                    try:
                        return float(d.get("value", default) or default)
                    except:
                        return default
            return default
        
        def get_fresh(key):
            for d in drivers:
                if d.get("key") == key:
                    return not d.get("stale", True)
            return False
        
        price_now = float(price.get("last") or 0.0)
        
        # Session one-hots
        sess = (sessions.get("current") or "").lower()
        sess_asia = (sess == "asia")
        sess_london = (sess == "london")
        sess_newyork = (sess == "newyork")
        sess_off = sess in ("", "none", None) or sess not in ("asia", "london", "newyork")
        
        # Quality buckets
        q_state = (quality.get("state") or "").upper()
        q_ok = (q_state == "OK")
        q_degraded = (q_state == "DEGRADED")
        q_poor = (q_state == "POOR")
        
        # Insert into DB
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO ml_features (
                        price_now, dxy_z, real_z, vix_z, risk_z, nom_z, do_ctx, mom,
                        range_to_atr20, activity, vol_pct, spread_pts, news_lock,
                        gap_pct, pct24h,
                        sess_asia, sess_london, sess_newyork, sess_off,
                        q_ok, q_degraded, q_poor,
                        dxyz_fresh, realz_fresh, vixz_fresh, risk_on_fresh,
                        nominalz_fresh, do_ctx_fresh, mom_fresh
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                """, (
                    price_now,
                    get_driver("dxyZ", 0.0),
                    get_driver("realZ", 0.0),
                    get_driver("vixZ", 0.0),
                    get_driver("risk_on", 0.0),
                    get_driver("nominalZ", 0.0),
                    get_driver("do_ctx", 0.0),
                    get_driver("mom", 0.0),
                    float(metrics.get("range_to_atr20") or 0.0),
                    float(metrics.get("activity_index") or 0.0),
                    int(metrics.get("volume_percentile") or 0),
                    int(quality.get("spread_pts") or 0),
                    bool(gates.get("news_lock")),
                    float(metrics.get("gap_pct") or 0.0),
                    float(price.get("pct24h") or 0.0),
                    sess_asia, sess_london, sess_newyork, sess_off,
                    q_ok, q_degraded, q_poor,
                    get_fresh("dxyZ"),
                    get_fresh("realZ"),
                    get_fresh("vixZ"),
                    get_fresh("risk_on"),
                    get_fresh("nominalZ"),
                    get_fresh("do_ctx"),
                    get_fresh("mom"),
                ))
                conn.commit()
                log.info(f"✓ ML sample collected | price={price_now:.2f}")
    
    except Exception as e:
        log.error(f"Failed to collect ML sample: {e}")

async def collector_loop():
    """
    Background task that runs continuously collecting training data.
    """
    interval_sec = int(os.getenv("ML_COLLECTION_INTERVAL", 300))
    
    if not DATABASE_URL:
        log.warning("ML collector disabled (no DATABASE_URL)")
        return
    
    # Ensure table exists
    ensure_ml_tables()
    
    log.info(f"🤖 ML collector started (interval={interval_sec}s)")
    
    iteration = 0
    while True:
        try:
            await collect_one_sample()
            iteration += 1
        except Exception as e:
            log.error(f"Collection error: {e}")
        
        await asyncio.sleep(interval_sec)

def start_background_collector():
    """Start the ML collector as a background task (call from startup)."""
    if not DATABASE_URL:
        return
    
    async def _wrapper():
        await collector_loop()
    
    asyncio.create_task(_wrapper())
    log.info("ML background collector scheduled")


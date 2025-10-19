"""
Background ML data collector for Render deployment.
Runs continuously, collecting features every 5 minutes and storing to PostgreSQL.
"""
import asyncio
import httpx
import json
import os
import time
from datetime import datetime, timezone
import logging
import psycopg2
import pandas as pd

log = logging.getLogger("ml_collector")

DATABASE_URL = os.getenv("DATABASE_URL")
BACKEND_URL = os.getenv("RENDER_EXTERNAL_URL") # Render provides this for internal service-to-service calls
if not BACKEND_URL:
    BACKEND_URL = "http://localhost:8787" # Fallback for local testing

COLLECTION_INTERVAL_SEC = int(os.getenv("ML_COLLECTION_INTERVAL", "300")) # Default 5 minutes

# Ensure the ml_features table exists
def _ensure_db_table():
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set, ML collection disabled.")
        return False
    try:
        with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
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
                    CREATE INDEX IF NOT EXISTS idx_ml_features_ts ON ml_features(ts DESC);
                """)
            conn.commit()
        log.info("Ensured ml_features table exists.")
        return True
    except Exception as e:
        log.error(f"Failed to ensure ml_features table: {e}")
        return False

# Helper to extract features from /home payload
def _extract_features(payload: dict) -> dict:
    price_data = payload.get("price", {})
    metrics_data = payload.get("metrics", {})
    quality_data = payload.get("quality", {})
    sessions_data = payload.get("sessions", {})
    gates_data = payload.get("gates", {})
    nowcast_data = metrics_data.get("nowcast", {})
    drivers = nowcast_data.get("drivers", [])

    def _get_driver_value(key, default=0.0):
        for d in drivers:
            if d.get("key") == key:
                try:
                    return float(d.get("value", default) or default)
                except (ValueError, TypeError):
                    return default
        return default

    def _get_driver_freshness(key, default=True):
        for d in drivers:
            if d.get("key") == key:
                return d.get("stale", not default) is not True # Invert stale to fresh
        return default

    features = {
        "ts": datetime.fromtimestamp(price_data.get("updatedAt", time.time()*1000) / 1000, tz=timezone.utc),
        "price_now": price_data.get("last"),
        "dxy_z": _get_driver_value("dxyZ"),
        "real_z": _get_driver_value("realZ"),
        "vix_z": _get_driver_value("vixZ"),
        "risk_z": _get_driver_value("risk_on"),
        "nom_z": _get_driver_value("nominalZ"),
        "do_ctx": _get_driver_value("do_ctx"),
        "mom": _get_driver_value("mom"),
        "range_to_atr20": metrics_data.get("range_to_atr20"),
        "activity": metrics_data.get("activity_index"),
        "vol_pct": metrics_data.get("volume_percentile"),
        "spread_pts": quality_data.get("spread_pts"),
        "news_lock": gates_data.get("news_lock", False),
        "gap_pct": metrics_data.get("gap_pct"),
        "pct24h": price_data.get("pct24h"),
        "sess_asia": sessions_data.get("current") == "asia",
        "sess_london": sessions_data.get("current") == "london",
        "sess_newyork": sessions_data.get("current") == "newyork",
        "sess_off": sessions_data.get("current") not in ["asia", "london", "newyork"],
        "q_ok": quality_data.get("state") == "OK",
        "q_degraded": quality_data.get("state") == "DEGRADED",
        "q_poor": quality_data.get("state") == "POOR",
        "dxyz_fresh": _get_driver_freshness("dxyZ"),
        "realz_fresh": _get_driver_freshness("realZ"),
        "vixz_fresh": _get_driver_freshness("vixZ"),
        "risk_on_fresh": _get_driver_freshness("risk_on"),
        "nominalz_fresh": _get_driver_freshness("nominalZ"),
        "do_ctx_fresh": _get_driver_freshness("do_ctx"),
        "mom_fresh": _get_driver_freshness("mom"),
    }
    return features

async def collect_and_store_features():
    if not _ensure_db_table():
        return

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30) as client:
        while True:
            try:
                log.info("Fetching /home for ML features...")
                response = await client.get("/home?nocache=true")
                response.raise_for_status()
                home_payload = response.json()
                features = _extract_features(home_payload)

                # CRITICAL: Guard against bad data when providers are down
                price = features.get("price_now")
                if price is None or price <= 0:
                    log.warning(f"Skipping ML sample due to invalid price: {price}")
                    await asyncio.sleep(COLLECTION_INTERVAL_SEC)
                    continue

                # Convert None to Python None for DB insertion
                for k, v in features.items():
                    if pd.isna(v):
                        features[k] = None

                with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO ml_features (
                                ts, price_now, dxy_z, real_z, vix_z, risk_z, nom_z, do_ctx, mom,
                                range_to_atr20, activity, vol_pct, spread_pts, news_lock, gap_pct, pct24h,
                                sess_asia, sess_london, sess_newyork, sess_off,
                                q_ok, q_degraded, q_poor,
                                dxyz_fresh, realz_fresh, vixz_fresh, risk_on_fresh, nominalz_fresh, do_ctx_fresh, mom_fresh
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s
                            )
                        """, (
                            features["ts"], features["price_now"], features["dxy_z"], features["real_z"],
                            features["vix_z"], features["risk_z"], features["nom_z"], features["do_ctx"],
                            features["mom"], features["range_to_atr20"], features["activity"],
                            features["vol_pct"], features["spread_pts"], features["news_lock"],
                            features["gap_pct"], features["pct24h"],
                            features["sess_asia"], features["sess_london"], features["sess_newyork"],
                            features["sess_off"], features["q_ok"], features["q_degraded"], features["q_poor"],
                            features["dxyz_fresh"], features["realz_fresh"], features["vixz_fresh"],
                            features["risk_on_fresh"], features["nominalz_fresh"], features["do_ctx_fresh"],
                            features["mom_fresh"]
                        ))
                    conn.commit()
                log.info(f"✓ ML sample collected | price={features['price_now']:.2f}")
            except httpx.HTTPStatusError as e:
                log.error(f"HTTP error fetching /home: {e.response.status_code} - {e.response.text}")
            except httpx.RequestError as e:
                log.error(f"Network error fetching /home: {e}")
            except Exception as e:
                log.error(f"Error during ML feature collection: {e}")

            await asyncio.sleep(COLLECTION_INTERVAL_SEC)

_collector_task = None

def start_background_collector():
    global _collector_task
    if _collector_task is None:
        log.info("Starting ML data collector background task...")
        _collector_task = asyncio.create_task(collect_and_store_features())
    else:
        log.info("ML data collector already running.")

def stop_background_collector():
    global _collector_task
    if _collector_task:
        log.info("Stopping ML data collector background task...")
        _collector_task.cancel()
        _collector_task = None


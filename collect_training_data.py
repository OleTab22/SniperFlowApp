#!/usr/bin/env python3
"""
Collect live training data from your SniperFlow backend.
Run this for several days/weeks to build a dataset, then train with train_model.py
"""
import asyncio
import httpx
import json
import csv
from datetime import datetime
from pathlib import Path

# Your backend URL
BACKEND_URL = "http://localhost:8787"  # adjust if needed

async def fetch_home():
    """Fetch /home endpoint and extract features + label."""
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{BACKEND_URL}/home")
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as e:
            print(f"Error fetching /home: {e}")
            return None

def extract_features(home_data):
    """Extract ML features from /home payload (matches your feature builder)."""
    if not home_data:
        return None
    
    price = home_data.get("price", {})
    metrics = home_data.get("metrics", {})
    quality = home_data.get("quality", {})
    sessions = home_data.get("sessions", {})
    gates = home_data.get("gates", {})
    nowcast_m = metrics.get("nowcast", {}) or {}
    drivers = nowcast_m.get("drivers", []) or []
    
    # Helper to get driver values
    def get_driver(key, default=0.0):
        for d in drivers:
            if d.get("key") == key:
                try:
                    return float(d.get("value", default) or default)
                except:
                    return default
        return default
    
    # Core features
    feats = {
        "dxy_z": get_driver("dxyZ", 0.0),
        "real_z": get_driver("realZ", 0.0),
        "vix_z": get_driver("vixZ", 0.0),
        "risk_z": get_driver("risk_on", 0.0),
        "nom_z": get_driver("nominalZ", 0.0),
        "do_ctx": get_driver("do_ctx", 0.0),
        "mom": get_driver("mom", 0.0),
        "range_to_atr20": float(metrics.get("range_to_atr20") or 0.0),
        "activity": float(metrics.get("activity_index") or 0.0),
        "vol_pct": float(metrics.get("volume_percentile") or 0.0),
        "spread_pts": float(quality.get("spread_pts") or 0.0),
        "news_lock": 1.0 if gates.get("news_lock") else 0.0,
        "gap_pct": float(metrics.get("gap_pct") or 0.0),
        "pct24h": float(price.get("pct24h") or 0.0),
    }
    
    # Freshness features
    for d in drivers:
        key = d.get("key")
        if key:
            feats[f"{key}_fresh"] = 0.0 if d.get("stale") else 1.0
    
    # Session one-hots
    sess = (sessions.get("current") or "").lower()
    feats["sess_asia"] = 1.0 if sess == "asia" else 0.0
    feats["sess_london"] = 1.0 if sess == "london" else 0.0
    feats["sess_newyork"] = 1.0 if sess == "newyork" else 0.0
    feats["sess_off"] = 1.0 if sess in ("", "none", None) or sess not in ("asia", "london", "newyork") else 0.0
    
    # Quality buckets
    q_state = (quality.get("state") or "").upper()
    feats["q_ok"] = 1.0 if q_state == "OK" else 0.0
    feats["q_degraded"] = 1.0 if q_state == "DEGRADED" else 0.0
    feats["q_poor"] = 1.0 if q_state == "POOR" else 0.0
    
    # Store current price for labeling later
    feats["_price_now"] = float(price.get("last") or 0.0)
    feats["_timestamp"] = datetime.now().isoformat()
    
    return feats

async def collect_loop(output_file="training_data_raw.csv", interval_sec=300):
    """
    Collect data every interval_sec (default 5 minutes).
    Save to CSV with all features.
    """
    Path(output_file).parent.mkdir(exist_ok=True, parents=True)
    
    # Check if file exists to determine if we need headers
    file_exists = Path(output_file).exists()
    
    print(f"Starting data collection → {output_file}")
    print(f"Interval: {interval_sec}s ({interval_sec/60:.1f} minutes)")
    print("Press Ctrl+C to stop\n")
    
    iteration = 0
    while True:
        try:
            home_data = await fetch_home()
            if home_data:
                feats = extract_features(home_data)
                if feats:
                    # Write to CSV
                    with open(output_file, 'a', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=sorted(feats.keys()))
                        if not file_exists:
                            writer.writeheader()
                            file_exists = True
                        writer.writerow(feats)
                    
                    iteration += 1
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Sample #{iteration} collected | Last={feats.get('_price_now', 0):.2f}")
            
            await asyncio.sleep(interval_sec)
        except KeyboardInterrupt:
            print(f"\n✓ Stopped. {iteration} samples collected → {output_file}")
            break
        except Exception as e:
            print(f"Error in collection loop: {e}")
            await asyncio.sleep(interval_sec)

if __name__ == "__main__":
    asyncio.run(collect_loop())


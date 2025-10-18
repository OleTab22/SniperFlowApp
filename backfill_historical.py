#!/usr/bin/env python3
"""
Backfill training data from historical OHLC bars.
This lets you train a model immediately without waiting for live collection.

Uses your backend's /v1/ohlc to get historical 5-minute bars,
then reconstructs approximate features at each timestamp.
"""
import asyncio
import httpx
import pandas as pd
from datetime import datetime, timedelta
import json

BACKEND_URL = "https://sniperflow-api.onrender.com"  # your deployed URL

async def fetch_historical_ohlc(symbol="XAUUSD", limit=5000):
    """Fetch recent OHLC bars from backend."""
    async with httpx.AsyncClient(timeout=60) as client:
        url = f"{BACKEND_URL}/v1/ohlc"
        params = {"symbol": symbol, "tf": "5m", "limit": limit}
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

def compute_features_from_bars(bars):
    """
    Build synthetic feature set from OHLC bars.
    This is approximate but gets you started.
    """
    features = []
    
    for i in range(100, len(bars) - 12):  # need lookback + lookahead
        bar = bars[i]
        
        # Simple momentum from recent bars
        recent = bars[i-20:i]
        if recent:
            mom = (bar['c'] - recent[0]['c']) / (recent[-1]['h'] - recent[-1]['l'] + 0.01)
        else:
            mom = 0.0
        
        # Range metrics
        day_bars = bars[max(0, i-100):i]
        if day_bars:
            day_hi = max(b['h'] for b in day_bars)
            day_lo = min(b['l'] for b in day_bars)
            range_val = (day_hi - day_lo) / (bar['c'] * 0.01) if bar['c'] > 0 else 0.0
        else:
            range_val = 0.0
        
        # Activity (volatility of returns)
        if len(recent) > 5:
            rets = [(recent[j]['c'] - recent[j-1]['c']) / recent[j-1]['c'] 
                    for j in range(1, len(recent)) if recent[j-1]['c'] > 0]
            activity = abs(sum(rets) / len(rets)) if rets else 0.0
        else:
            activity = 0.0
        
        # Label: did price go up in next 12 bars (1 hour)?
        future = bars[i + 12]
        target = 1 if future['c'] > bar['c'] else 0
        
        # Feature vector (simplified - real drivers would come from /v1/drivers)
        feat = {
            # Drivers (set to 0 for historical backfill - we don't have macro data)
            "dxy_z": 0.0,
            "real_z": 0.0,
            "vix_z": 0.0,
            "risk_z": 0.0,
            "nom_z": 0.0,
            "do_ctx": 0.0,
            "mom": mom,
            "range_to_atr20": range_val,
            "activity": activity * 100,  # scale up
            "vol_pct": 50,  # neutral
            "spread_pts": 15,  # assume typical
            "news_lock": 0.0,
            "gap_pct": 0.0,
            "pct24h": 0.0,
            # Sessions (set to off for backfill)
            "sess_asia": 0.0,
            "sess_london": 0.0,
            "sess_newyork": 0.0,
            "sess_off": 1.0,
            # Quality (assume OK)
            "q_ok": 1.0,
            "q_degraded": 0.0,
            "q_poor": 0.0,
            # Freshness (assume fresh)
            "dxyZ_fresh": 1.0,
            "realZ_fresh": 1.0,
            "vixZ_fresh": 1.0,
            "risk_on_fresh": 1.0,
            "nominalZ_fresh": 1.0,
            "do_ctx_fresh": 1.0,
            "mom_fresh": 1.0,
            # Target
            "target": target,
            "_timestamp": datetime.fromtimestamp(bar['t'] / 1000).isoformat(),
            "_price_now": bar['c'],
        }
        features.append(feat)
    
    return features

async def main():
    print("Fetching historical OHLC from backend...")
    data = await fetch_historical_ohlc(limit=5000)
    
    if not data or not data.get("bars"):
        print("ERROR: No bars returned from /v1/ohlc")
        return
    
    bars = data["bars"]
    print(f"Got {len(bars)} bars")
    
    print("Computing features...")
    features = compute_features_from_bars(bars)
    print(f"Created {len(features)} training samples")
    
    # Save to CSV
    df = pd.DataFrame(features)
    output_file = "training_data_backfill.csv"
    df.to_csv(output_file, index=False)
    
    print(f"✓ Saved to {output_file}")
    print(f"✓ Samples: {len(df)}")
    print(f"✓ Target distribution: {df['target'].value_counts().to_dict()}")
    print(f"\nNext: python label_and_train.py {output_file}")

if __name__ == "__main__":
    asyncio.run(main())


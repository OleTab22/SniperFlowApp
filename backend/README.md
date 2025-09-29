# SniperFlow Backend (FastAPI)

Provides intraday levels for XAUUSD using free data sources with session-aware calculations.

## Endpoints
- GET `/levels/intraday?symbol=XAUUSD`
  - `{ asOf, lastPrice, DO, PDH, PDL }`
- GET `/levels/intraday/sessions?symbol=XAUUSD`
  - `{ asOf, lastPrice, daily:{DO,PDH,PDL}, sessions:{ sydney:{...}, tokyo:{...}, london:{...}, newyork:{...} } }`

## Data sources
- Primary: Yahoo Finance (unofficial, no key)
- Fallback: Alpha Vantage (free; set `ALPHAVANTAGE_API_KEY`)

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
export ALPHAVANTAGE_API_KEY=your_key   # optional fallback
uvicorn backend.app:app --port 8787
```

Android is configured to call `http://10.0.2.2:8787/` inside the emulator.

## Notes
- Session rollover at 17:00 America/New_York; windows for Sydney/Tokyo/London/New York.
- 5-minute candles; DO = first candle open after session start.
- Simple in-memory cache (~15s) to reduce rate limits.



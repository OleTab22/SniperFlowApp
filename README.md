# SniperFlow (XAUUSD)

SniperFlow is a focused Android trading companion for gold (XAU/USD). It surfaces context you actually use intraday — bias and drivers, session timing, key levels — and gives you a fast, offline‑first journal. Educational tool only — not financial advice.

## Highlights
- Home dashboard
  - Live price, 24h change and sparkline
  - Bias Ring with Nowcast drivers (DXY, real yields, VIX, momentum)
  - Market metric chips (Gap %, Range×ATR20, Volume pctile, Activity)
  - Sessions tracker (Asia/London/New York) + session mid distance
  - Key levels pills (DO/PDH/PDL) with deltas
  - Quality state and provider status cues; plan‑lock banner
  - Optional WS ticks; graceful fallback to polling/cached
- Journal (Room + WorkManager)
  - Add/edit entries with tags and screenshots; swipe‑to‑delete
  - CSV export to Documents; offline first with background sync to backend `/v1/journal`
  - Detail screen with R:R, context, shots gallery
  - Quick‑journal FAB from Home
- Chart
  - TradingView embed loaded from `app/src/main/assets/chart.html`
  - Timeframe shortcuts (15m/1h/4h/1D) bridged via JS
- Settings
  - Epsilon (price step), cooldown (refresh throttle), timezone (affects sessions)
  - Optional “Test API” ping to `/health`
- Auth (optional)
  - Simple email/password with Firebase Auth (Login/Register screens)

## Project structure
- Android app: `app/`
  - Entry: `com.example.sniperflow.auth.LoginActivity`
  - Home: `com.example.sniperflow.MainActivity`
  - Journal: `com.example.sniperflow.ui.journal.*` (Room DB, DAO, UI)
  - Chart: `com.example.sniperflow.chart.ChartActivity` + `assets/chart.html`
  - Networking: `com.example.sniperflow.network.*` (Retrofit/Moshi/OkHttp)
  - Metrics/domain models: `com.example.sniperflow.domain.*`
  - Keep‑alive/polling: `ui.keepalive.BackendKeepAlive`, WS: `PriceWsClient`
- Backend (optional): `backend/`
  - FastAPI service exposing `/home`, levels, drivers/nowcast, journal endpoints
  - See `backend/README.md` for provider notes and local run instructions

## Key technologies
- Kotlin, AndroidX, Material Components
- Retrofit + Moshi + OkHttp (with timeouts)
- Room (KSP) + WorkManager for background sync
- WebView (TradingView embed) with console logging enabled in debug
- Coil for images, FlexboxLayout for driver chips, Timber for logs
- Firebase Auth (email/password) — can be disabled if not needed

## Configuration

### API base URL
The app uses `BuildConfig.BASE_URL` (set per build type in `app/build.gradle.kts`). Defaults to the hosted Render API:

```text
debug/release BASE_URL = https://sniperflow-api.onrender.com/
```

For local backend during emulator development, use the emulator loopback and ensure a trailing slash (Retrofit requirement):

```text
http://10.0.2.2:8787/
```

Change it in `app/build.gradle.kts`:

```startLine:endLine:SniperFlow/app/build.gradle.kts
// ... inside android { buildTypes { debug { ... } } }
buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8787/\"")
```

Notes:
- Manifest sets `android:usesCleartextTraffic="true"` to allow `http://` in dev.
- `RetrofitModule` normalizes the trailing slash, but keep it in the value to be explicit.

### Settings screen
- Epsilon: price step used by +/- steppers in the journal sheet
- Cooldown (ms): throttle for manual and auto refresh on Home
- Timezone: affects session calculations (default `Africa/Johannesburg`)
- Optional “Test API” button pings `/health` on the configured `BASE_URL`

## Running the Android app

Prereqs: Android Studio Jellyfish+ (or newer), Android SDK 36, JDK 11 (the project sets `jvmTarget = 11`).

1) Open this folder in Android Studio and let Gradle sync.
2) Build and run on an emulator or device.
3) If using a local backend, set `BASE_URL` to `http://10.0.2.2:8787/` and run the backend (see below).

Gradle CLI examples:

```bash
./gradlew :app:assembleDebug
./gradlew :app:installDebug
./gradlew test
```

Firebase note: a `google-services.json` is present. If you don’t want Firebase, you can remove the Google Services plugin and the Auth dependency, or keep them — core features work without sign‑in.

## Backend (optional, local)

FastAPI service lives in `backend/`. It consolidates free data sources and exposes:

- GET `/health` — liveness
- GET `/home` — consolidated payload (price, metrics, levels, sessions, alerts, gates)
- GET `/levels/intraday` and `/levels/intraday/sessions` — XAUUSD levels and session splits
- GET `/v1/levels/today` — DO/PDH/PDL for today (UTC‑anchored)
- GET `/v1/nowcast`, `/v1/drivers` — model score and driver chips (fallbacks)
- POST `/v1/journal` (PUT/DELETE variants) — receive journal entries from the app
- WS `/ticks` — optional price stream

See `backend/README.md` for API keys and exact commands. Quick start:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
export TWELVEDATA_API_KEY=your_key
export FRED_API_KEY=your_key
uvicorn backend.main:app --port 8787
```

With the Android emulator, `http://10.0.2.2:8787/` will map to the host.

## Using the app
- Home: pull to refresh or tap Refresh. Connection dot shows WS/Polling/Offline and stale hints.
- Session pills: tap to see minutes left; highlighting adapts to your timezone.
- Driver chips: tap for a brief explanation dialog.
- Journal: tap + to add; long‑press a list item to edit; swipe to delete.
- CSV export: in Journal screen menu (three dots). Files go to the app Documents directory, typically:

```text
/storage/emulated/0/Android/data/com.example.sniperflow/files/Documents/journal-YYYYMMDD-HHmm.csv
```

## Testing

Run unit tests:

```bash
./gradlew test
```

Notable tests live in `app/src/test/java/com/example/sniperflow/`:
- `AlertEngineTest.kt` — alert cooldown/side transitions
- `RrCalcTest.kt` — R:R calculator
- `GatesTest.kt` — trade gates actionable logic
- `CountdownTest.kt` — countdown math

## Troubleshooting
- API cold starts (Render free tier) — first `/home` may be slow; the app shows “Degraded/Offline” banners and falls back to cached UI.
- Base URL must be reachable and end with `/`.
- Emulator local backend requires `http://10.0.2.2:8787/` (not `localhost`).
- Cleartext HTTP allowed only for dev; use HTTPS for production.
- If Firebase Auth causes build issues, temporarily remove the Google Services plugin and Auth dependency.

## License & disclaimer
- License: see `LICENSE` in the repository.
- Disclaimer: For education only. No financial advice; use at your own risk.


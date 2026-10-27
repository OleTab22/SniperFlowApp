# SniperFlow

**An Android trading companion built around XAU/USD (gold).**

SniperFlow brings market context, session timing, risk checks, and a trade journal into one mobile experience. The aim is to help traders follow a consistent process: prepare for a session, assess conditions, review alerts, and record their decisions.

Built with **Kotlin/Android** and an optional **Python/FastAPI** backend. This is a personal software project and educational tool—not a trade execution platform or financial advice.

## What it does

| Area | Capabilities |
| --- | --- |
| **Market dashboard** | XAU/USD price context, 24-hour change, session countdowns, and reference levels such as daily open, previous-day high, and previous-day low |
| **Market context** | Bias drivers (including DXY, yields, VIX, and momentum), market-regime information, and provider-quality indicators |
| **Risk-aware alerts** | Trade gates, Plan-Lock thresholds, and visible reasons when conditions do not qualify |
| **Trade journal** | Create and edit entries, capture risk/reward context and screenshots, export CSV, and queue background synchronization |
| **Chart & onboarding** | Embedded TradingView chart, timeframe shortcuts, and guided first-run experience |
| **Personalisation** | English, isiZulu, and Afrikaans interface resources; notification preferences and time-zone settings |

Market-data features depend on configured providers and network availability. The interface includes cached or degraded states when live information is unavailable.

## Technology and architecture

- **Android:** Kotlin, AndroidX, Material Components, Jetpack Compose, Retrofit, OkHttp, Moshi
- **Local data:** Room database and WorkManager for offline-first journaling and queued sync
- **Backend:** FastAPI with endpoints for price context, intraday levels, driver data, and journal records
- **Integrations:** TradingView chart embed and Firebase Authentication support
- **Quality:** Gradle build configuration, unit-test sources, and GitHub Actions workflows

```text
Android app (Kotlin)
  ├── Dashboard, alerts, chart, settings
  ├── Room journal + WorkManager sync
  └── Retrofit / WebSocket client
          │
          ▼
FastAPI backend (Python)
  ├── Market context and levels
  ├── Drivers / regime endpoints
  └── Journal API
          │
          ▼
External market-data providers (when configured)
```

## Repository layout

| Path | Purpose |
| --- | --- |
| [`app/`](app/) | Android app, UI, networking, local journal, and unit tests |
| [`backend/`](backend/) | Python API and its [setup notes](backend/README.md) |
| [`models/`](models/) | Model-related project assets |
| [`TRAINING_GUIDE.md`](TRAINING_GUIDE.md) | Experimental model-training workflow |
| [`.github/workflows/`](.github/workflows/) | CI configuration |

## Run the Android app

**Requirements:** Android Studio, JDK 17, and Android SDK 36. The project compiles Kotlin/JVM code to Java 11 bytecode.

1. Clone the repository and open its root directory in Android Studio.

   ```bash
   git clone https://github.com/OleTab22/SniperFlowApp.git
   cd SniperFlowApp
   ```

2. Configure Firebase for the current build. The app applies the Google Services Gradle plugin, but `google-services.json` is excluded from version control. Supply the configuration for your own Firebase project at `app/google-services.json`. Never commit private credentials.
3. Let Gradle sync, select an Android emulator or device, and run the `app` configuration.

For command-line development:

```bash
./gradlew :app:assembleDebug
./gradlew test
```

On Windows, use `gradlew.bat` instead of `./gradlew`.

### API configuration

The Android app reads `BuildConfig.BASE_URL` from [`app/build.gradle.kts`](app/build.gradle.kts). Its current default is:

```text
https://sniperflow-api.onrender.com/
```

To use your own backend from an Android emulator, change the relevant `buildConfigField` to:

```kotlin
buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8787/\"")
```

`10.0.2.2` is the emulator's route to your development computer. Retain the trailing slash required by Retrofit. Prefer HTTPS outside local development.

## Run the FastAPI backend

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
export TWELVEDATA_API_KEY=your_key
export FRED_API_KEY=your_key
uvicorn backend.main:app --port 8787
```

On Windows, activate the virtual environment with `.venv\Scripts\activate` and set environment variables with the syntax for your shell. See the [backend README](backend/README.md) for data-source and endpoint details. Never commit real API keys.

## Testing and project status

Unit-test sources are in [`app/src/test/`](app/src/test/). Run them with:

```bash
./gradlew test
```

The repository also contains Android build and test automation in [GitHub Actions](https://github.com/OleTab22/SniperFlowApp/actions). **Check its latest run results before relying on a green build**; not all historical Android workflow runs have passed. An example Firebase configuration may be needed for reproducible builds.

The app is an evolving project. External data providers can be rate-limited or unavailable, the hosted API may take time to start, and market context must not be treated as a guaranteed signal. No live trading or return claims are made here.

## License

See [LICENSE](LICENSE). SniperFlow is for educational and software-development purposes only, not financial advice.

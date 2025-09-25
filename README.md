# SniperFlow (XAU/USD only)

SniperFlow is a gold-only Android trading assistant that enforces discipline with **glass-box alerts** (drivers + weights) and **risk/news gates**, plus a fast **1-tap journal**. Times default to SAST. *Educational tool — not financial advice.*

## MVP Features
- Onboarding (sessions, risk limits, News Lock, language)
- Home: Bias Ring + driver chips, session clock, next red-news timer, DO/PDH/PDL
- Alerts: Info/Setup/Actionable with gate receipts and plan suggestion
- Chart overlays: Daily Open, PDH/PDL, Asia/London/NY shading, London Fix
- Journal: pre-filled from alerts; offline queue → sync

## Tech 
- Kotlin, Clean Architecture (domain/data/ui), Retrofit, Room, WorkManager, FCM

## Repo layout 

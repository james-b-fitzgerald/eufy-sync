# eufy-sync Android App

An Android wrapper for [eufy-sync](../README.md) that runs the core Python sync
logic on-device via [Chaquopy](https://chaquo.com/chaquopy/) (CPython 3.12
embedded in the APK) and schedules a daily background sync using WorkManager.

## How it works

```
TokenStore (EncryptedSharedPreferences)
        │
        ▼
SyncWorker (WorkManager — once per day, network required)
        │  calls via Chaquopy
        ▼
android_bridge.run_sync()          ← eufy_sync/android_bridge.py
        │  delegates to unchanged Python
        ├─► eufy_client.py          (Eufy HTTP login + /v1/device/data)
        ├─► garmin_auth.py          (DI OAuth2 token refresh — no Playwright)
        ├─► garmin_client.py        (FIT file upload)
        ├─► strava_client.py        (weight PUT + token refresh)
        ├─► fit.py                  (binary FIT encoder)
        ├─► transform.py            (range validation + field mapping)
        └─► state.py                (SQLite deduplication watermark)
```

### Android-only components

| Component | Purpose |
|-----------|---------|
| `GarminAuthActivity` | WebView + JS injection to capture Garmin `serviceTicketId` — same technique as `garmin_auth.browser_login()` on macOS |
| `StravaCallbackActivity` | Handles `eufysync://strava/callback` OAuth redirect from Chrome Custom Tabs |
| `SyncWorker` | `CoroutineWorker` scheduled by WorkManager (24 h, network required) |
| `TokenStore` | `EncryptedSharedPreferences` (AES256-GCM via Android Keystore) |
| `SetupActivity` | Multi-step wizard: Eufy creds → target selection → Garmin WebView auth → Strava OAuth |
| `MainActivity` | Status screen: last sync time, token health, sync-now button, auto-sync toggle |
| `ParseUtils` | Pure-Kotlin JSON/URI helpers (testable without Robolectric) |

## Prerequisites

- Android Studio Hedgehog or newer
- Android SDK 34 + NDK (for Chaquopy native libs)
- `arm64-v8a` device or `x86_64` emulator

## Build

Build the debug APK with the Gradle wrapper from this repo:

```bash
cd android/
./gradlew assembleDebug
```

Chaquopy automatically downloads CPython 3.12 and pip-installs `httpx` and
`pyyaml` during the first build (requires internet access).

## Run unit tests

The Kotlin unit tests in `app/src/test/` have no Android dependencies and run
on the JVM:

```bash
./gradlew :app:test
```

The Python bridge tests live in the repo root and use the standard test runner:

```bash
cd ..
python -m pytest tests/test_android_bridge.py -v
```

## First-run flow

1. Open the app → Setup wizard opens automatically.
2. Enter Eufy email + password.
3. Select targets (Garmin, Strava, or both).
4. **Garmin**: enter Garmin email → tap *Open Garmin Login* → a WebView
   loads the Garmin SSO page; log in there with your Garmin credentials; the
   app intercepts the service ticket and exchanges it for OAuth2 tokens
   automatically.
5. **Strava**: enter your Strava API app's Client ID and Secret → tap
   *Authorize Strava* → Chrome opens; grant access; the app handles the
   `eufysync://strava/callback` redirect.
6. Tap *Finish Setup*. The daily sync is scheduled immediately.

## Re-authentication

Garmin refresh tokens last ~1 year. When they expire, `SyncWorker` will fail
and post a notification. Tap *Configure* in the main screen to open the wizard
and redo the Garmin WebView login.

## Security notes

- All credentials and OAuth tokens are stored in `EncryptedSharedPreferences`
  (Android Keystore-backed AES256-GCM).
- Token JSON files written by Python (`session.json`, `strava_token.json`,
  `eufy_token.json`) are in the app's private `filesDir` and excluded from
  cloud backup via `backup_rules.xml` / `data_extraction_rules.xml`.
- HTTPS is enforced by `network_security_config.xml` (cleartext blocked).
- Strava OAuth uses a cryptographically random CSRF state value.

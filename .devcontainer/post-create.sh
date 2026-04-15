#!/usr/bin/env bash
# .devcontainer/post-create.sh
#
# Runs once after the dev container is created.  Sets up everything needed to
# build the Android app and run all tests inside a GitHub Codespace:
#
#   • Android SDK packages (platform-tools, android-34, build-tools, NDK r25)
#   • Android emulator + x86_64 system image (only when /dev/kvm is present)
#   • Gradle wrapper for the android/ sub-project
#   • Python 3.12 dependencies (eufy_sync + pytest)

set -euo pipefail

ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}"

echo "──────────────────────────────────────────────────────────────────────────"
echo "  eufy-sync Android – post-create setup"
echo "──────────────────────────────────────────────────────────────────────────"

# ── 1. Accept SDK licences ────────────────────────────────────────────────────
echo "[1/5] Accepting Android SDK licences..."
yes | sdkmanager --licenses > /dev/null 2>&1 || true

# ── 2. Install essential SDK packages ────────────────────────────────────────
# platform-tools  → adb
# platforms;android-34 + build-tools;34.0.0  → compile the APK
# ndk;25.2.9519653 → Chaquopy native bridge (Chaquopy 15.x targets NDK r25)
echo "[2/5] Installing SDK packages (platform-tools, android-34, build-tools, NDK r25)..."
sdkmanager \
    "platform-tools" \
    "platforms;android-34" \
    "build-tools;34.0.0" \
    "ndk;25.2.9519653"

# ── 3. Emulator + AVD (requires KVM — available on Codespaces 4-core+) ───────
echo "[3/5] Checking KVM availability for Android emulator..."
if [ -e /dev/kvm ]; then
    echo "  KVM detected — installing emulator and system image..."
    sdkmanager \
        "emulator" \
        "system-images;android-34;google_apis;x86_64"

    # Create an AVD named 'eufy_test' (Pixel 6 form-factor, API 34 x86_64)
    echo "no" | avdmanager create avd \
        --name    "eufy_test" \
        --package "system-images;android-34;google_apis;x86_64" \
        --device  "pixel_6" \
        --force
    echo "  AVD 'eufy_test' created."
else
    echo "  /dev/kvm not found — skipping emulator install."
    echo "  The APK can still be built and JVM tests run without an emulator."
    echo "  For emulator support, open this Codespace on a 4-core (or larger) machine."
fi

# ── 4. Gradle wrapper validation ──────────────────────────────────────────────
# Wrapper files are committed, so just ensure they are executable/present.
echo "[4/5] Validating committed Gradle wrapper..."
if [ ! -f "android/gradlew" ]; then
    echo "  ERROR: android/gradlew is missing."
    exit 1
fi
chmod +x "android/gradlew"

if [ ! -f "android/gradle/wrapper/gradle-wrapper.properties" ]; then
    echo "  ERROR: android/gradle/wrapper/gradle-wrapper.properties is missing."
    exit 1
fi
echo "  Using committed wrapper in android/."

# ── 5. Python dependencies ────────────────────────────────────────────────────
echo "[5/5] Installing Python dependencies..."
pip install --quiet -e .
pip install --quiet pytest

echo ""
echo "──────────────────────────────────────────────────────────────────────────"
echo "  ✅  Setup complete!  Quick-start commands:"
echo ""
echo "  Build APK          cd android && ./gradlew assembleDebug"
echo "  JVM unit tests     cd android && ./gradlew :app:test"
echo "  Python unit tests  pytest tests/ -v"
if [ -e /dev/kvm ]; then
echo "  Start emulator     emulator -avd eufy_test -no-snapshot &"
echo "  Install APK        adb install android/app/build/outputs/apk/debug/app-debug.apk"
fi
echo "──────────────────────────────────────────────────────────────────────────"

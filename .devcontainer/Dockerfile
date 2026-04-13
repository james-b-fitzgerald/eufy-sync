FROM mcr.microsoft.com/devcontainers/java:17-bullseye

# ── Android command-line tools ────────────────────────────────────────────────
# Sets ANDROID_HOME and puts sdkmanager / avdmanager on PATH.
# Heavy SDK packages (NDK, system image) are downloaded in post-create.sh so
# that the image itself stays small and builds quickly.

ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=${PATH}:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/emulator

# cmdline-tools 13.0  (stable, widely used in CI)
ARG CMDTOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends wget unzip \
    && rm -rf /var/lib/apt/lists/* \
    # Download and install cmdline-tools
    && mkdir -p ${ANDROID_HOME}/cmdline-tools \
    && wget -q "${CMDTOOLS_URL}" -O /tmp/cmdtools.zip \
    && unzip -q /tmp/cmdtools.zip -d /tmp/cmdtools_extract \
    && mv /tmp/cmdtools_extract/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
    && rm -rf /tmp/cmdtools.zip /tmp/cmdtools_extract \
    # Make the SDK directory writable by the default non-root user (vscode uid=1000)
    && chown -R 1000:1000 ${ANDROID_HOME}

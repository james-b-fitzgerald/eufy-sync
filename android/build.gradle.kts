// Top-level build file.  Plugin declarations only; no project-wide dependencies.
plugins {
    id("com.android.application")   version "8.2.2"  apply false
    id("org.jetbrains.kotlin.android") version "1.9.22" apply false
    // Chaquopy embeds CPython 3.12 and pip packages into the APK.
    // All eufy_sync sync logic runs as Python; Kotlin is a thin Android wrapper.
    id("com.chaquo.python")         version "15.0.1" apply false
}

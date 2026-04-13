plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.eufysync.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.eufysync.android"
        minSdk = 26          // Android 8.0 — required for EncryptedSharedPreferences
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // ---------- Chaquopy configuration ----------------------------------------
        // Python 3.12 is embedded in the APK.  All eufy_sync core logic runs here;
        // Kotlin only handles Android UI / WorkManager / EncryptedSharedPreferences.
        ndk {
            // Limit ABIs to reduce APK size; add "x86_64" for emulators if needed.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }

        python {
            version = "3.12"
            pip {
                // Runtime deps of eufy_sync (playwright + keyring are NOT needed on Android)
                install("httpx>=0.27.0")
                install("pyyaml>=6.0")
            }
        }
    }

    // Make the eufy_sync Python package (at the repo root) visible to Chaquopy.
    // rootProject.projectDir = android/  →  ../ = repo root where eufy_sync/ lives.
    sourceSets {
        getByName("main") {
            python.srcDirs(rootProject.projectDir.parent)
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    buildFeatures {
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // AndroidX core
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.activity:activity-ktx:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // Secure credential storage (EncryptedSharedPreferences)
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // Background sync (WorkManager)
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // Chrome Custom Tabs — used for Strava OAuth browser flow
    implementation("androidx.browser:browser:1.7.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Gson — JSON parsing of python bridge results in Kotlin
    implementation("com.google.code.gson:gson:2.10.1")

    // Unit tests (JVM — no Android runtime required)
    testImplementation("junit:junit:4.13.2")
    testImplementation("com.google.code.gson:gson:2.10.1")

    // Instrumented tests
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
}

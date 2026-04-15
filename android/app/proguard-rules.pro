# Add project-specific ProGuard rules here.
# By default, the flags in the Android SDK's proguard-android-optimize.txt are used.

# Keep Chaquopy Python runtime
-keep class com.chaquo.python.** { *; }

# Keep WorkManager workers
-keep class com.eufysync.android.sync.SyncWorker { *; }

# Keep EncryptedSharedPreferences / Tink
-keep class com.google.crypto.tink.** { *; }

# Gson: keep data classes used for JSON deserialization
-keep class com.eufysync.android.ParseUtils$* { *; }
-keepattributes Signature
-dontwarn com.google.gson.**

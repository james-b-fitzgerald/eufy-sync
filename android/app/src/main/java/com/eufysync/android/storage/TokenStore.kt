package com.eufysync.android.storage

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Encrypted credential and flag storage backed by [EncryptedSharedPreferences].
 *
 * All secrets (passwords, OAuth tokens, API keys) are stored with AES256-GCM
 * encryption via Android Keystore.  Plain strings (email addresses, target
 * selections) are stored in the same encrypted store for simplicity.
 *
 * The data directory path passed to the Python bridge is derived from
 * [Context.filesDir] at call time; it is not persisted here.
 */
class TokenStore(context: Context) {

    private val prefs: SharedPreferences = buildEncryptedPrefs(context)

    // ── Eufy ─────────────────────────────────────────────────────────────────

    var eufyEmail: String?
        get() = prefs.getString(KEY_EUFY_EMAIL, null)
        set(v) = prefs.edit().putString(KEY_EUFY_EMAIL, v).apply()

    var eufyPassword: String?
        get() = prefs.getString(KEY_EUFY_PASSWORD, null)
        set(v) = prefs.edit().putString(KEY_EUFY_PASSWORD, v).apply()

    // ── Garmin ───────────────────────────────────────────────────────────────

    var garminEmail: String?
        get() = prefs.getString(KEY_GARMIN_EMAIL, null)
        set(v) = prefs.edit().putString(KEY_GARMIN_EMAIL, v).apply()

    /** Garmin password — stored so it can be passed to the Python bridge for token refresh. */
    var garminPassword: String?
        get() = prefs.getString(KEY_GARMIN_PASSWORD, null)
        set(v) = prefs.edit().putString(KEY_GARMIN_PASSWORD, v).apply()

    /** True once GarminAuthActivity has successfully exchanged the service ticket. */
    var garminAuthed: Boolean
        get() = prefs.getBoolean(KEY_GARMIN_AUTHED, false)
        set(v) = prefs.edit().putBoolean(KEY_GARMIN_AUTHED, v).apply()

    // ── Strava ───────────────────────────────────────────────────────────────

    var stravaClientId: String?
        get() = prefs.getString(KEY_STRAVA_CLIENT_ID, null)
        set(v) = prefs.edit().putString(KEY_STRAVA_CLIENT_ID, v).apply()

    var stravaClientSecret: String?
        get() = prefs.getString(KEY_STRAVA_CLIENT_SECRET, null)
        set(v) = prefs.edit().putString(KEY_STRAVA_CLIENT_SECRET, v).apply()

    /** Random state value generated before opening the Strava browser tab. */
    var stravaCsrfState: String?
        get() = prefs.getString(KEY_STRAVA_CSRF_STATE, null)
        set(v) = prefs.edit().putString(KEY_STRAVA_CSRF_STATE, v).apply()

    /** True once StravaCallbackActivity has successfully exchanged the OAuth code. */
    var stravaAuthed: Boolean
        get() = prefs.getBoolean(KEY_STRAVA_AUTHED, false)
        set(v) = prefs.edit().putBoolean(KEY_STRAVA_AUTHED, v).apply()

    // ── Sync targets ─────────────────────────────────────────────────────────

    var syncGarmin: Boolean
        get() = prefs.getBoolean(KEY_SYNC_GARMIN, true)
        set(v) = prefs.edit().putBoolean(KEY_SYNC_GARMIN, v).apply()

    var syncStrava: Boolean
        get() = prefs.getBoolean(KEY_SYNC_STRAVA, false)
        set(v) = prefs.edit().putBoolean(KEY_SYNC_STRAVA, v).apply()

    // ── App state ────────────────────────────────────────────────────────────

    /** Set to true when the setup wizard completes successfully. */
    var setupComplete: Boolean
        get() = prefs.getBoolean(KEY_SETUP_COMPLETE, false)
        set(v) = prefs.edit().putBoolean(KEY_SETUP_COMPLETE, v).apply()

    var autoSyncEnabled: Boolean
        get() = prefs.getBoolean(KEY_AUTO_SYNC, true)
        set(v) = prefs.edit().putBoolean(KEY_AUTO_SYNC, v).apply()

    /** ISO-8601 timestamp of the last successful sync, or null. */
    var lastSyncTime: String?
        get() = prefs.getString(KEY_LAST_SYNC_TIME, null)
        set(v) = prefs.edit().putString(KEY_LAST_SYNC_TIME, v).apply()

    var lastSyncSummary: String?
        get() = prefs.getString(KEY_LAST_SYNC_SUMMARY, null)
        set(v) = prefs.edit().putString(KEY_LAST_SYNC_SUMMARY, v).apply()

    // ── Helpers ──────────────────────────────────────────────────────────────

    /** Returns true if the minimum credentials required to run a sync are present. */
    fun hasMinimumCredentials(): Boolean {
        if (eufyEmail.isNullOrBlank() || eufyPassword.isNullOrBlank()) return false
        if (syncGarmin && !garminAuthed) return false
        if (syncStrava && !stravaAuthed) return false
        return syncGarmin || syncStrava
    }

    private companion object {
        const val PREFS_FILE = "eufy_sync_secure"

        const val KEY_EUFY_EMAIL       = "eufy_email"
        const val KEY_EUFY_PASSWORD    = "eufy_password"
        const val KEY_GARMIN_EMAIL     = "garmin_email"
        const val KEY_GARMIN_PASSWORD  = "garmin_password"
        const val KEY_GARMIN_AUTHED    = "garmin_authed"
        const val KEY_STRAVA_CLIENT_ID     = "strava_client_id"
        const val KEY_STRAVA_CLIENT_SECRET = "strava_client_secret"
        const val KEY_STRAVA_CSRF_STATE    = "strava_csrf_state"
        const val KEY_STRAVA_AUTHED        = "strava_authed"
        const val KEY_SYNC_GARMIN    = "sync_garmin"
        const val KEY_SYNC_STRAVA    = "sync_strava"
        const val KEY_SETUP_COMPLETE = "setup_complete"
        const val KEY_AUTO_SYNC      = "auto_sync"
        const val KEY_LAST_SYNC_TIME    = "last_sync_time"
        const val KEY_LAST_SYNC_SUMMARY = "last_sync_summary"

        fun buildEncryptedPrefs(context: Context): SharedPreferences {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            return EncryptedSharedPreferences.create(
                context,
                PREFS_FILE,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        }
    }
}

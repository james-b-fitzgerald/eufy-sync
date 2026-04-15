package com.eufysync.android.auth

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.eufysync.android.ParseUtils
import com.eufysync.android.storage.TokenStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.lifecycle.lifecycleScope

/**
 * Transparent trampoline activity that handles the Strava OAuth callback URI.
 *
 * Registered in the manifest with an intent filter for `eufysync://strava/callback`.
 * When Strava redirects there after the user authorises in Chrome Custom Tabs,
 * Android routes the intent here.
 *
 * Flow:
 * 1. Read `code` and `state` from the URI query string.
 * 2. Verify CSRF `state` against [TokenStore.stravaCsrfState].
 * 3. Call `android_bridge.exchange_strava_code()` (pure httpx via Chaquopy).
 * 4. Mark [TokenStore.stravaAuthed] = true, clear the CSRF state.
 * 5. Finish with [Activity.RESULT_OK] — [SetupActivity] receives the result.
 *
 * Mirrors [strava_client.authorize_strava()][eufy_sync.strava_client.authorize_strava]
 * which on macOS runs a local HTTP server to capture the same callback.
 */
class StravaCallbackActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "StravaCallback"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val uri = intent?.data
        if (uri == null) {
            Log.w(TAG, "No URI in intent — finishing")
            finish()
            return
        }

        handleCallback(uri)
    }

    private fun handleCallback(uri: Uri) {
        val store = TokenStore(this)
        val uriString = uri.toString()
        val expectedState = store.stravaCsrfState

        if (expectedState.isNullOrBlank()) {
            Log.e(TAG, "No CSRF state stored — possible replay attack, ignoring callback")
            setResult(Activity.RESULT_CANCELED)
            finish()
            return
        }

        val code = ParseUtils.extractStravaCode(uriString, expectedState)
        if (code == null) {
            Log.e(TAG, "State mismatch or missing code in Strava callback: $uriString")
            setResult(Activity.RESULT_CANCELED)
            finish()
            return
        }

        val clientId     = store.stravaClientId     ?: return failWith("Strava clientId missing")
        val clientSecret = store.stravaClientSecret ?: return failWith("Strava clientSecret missing")
        val dataDir      = "${filesDir.absolutePath}/.garmin-sync"

        lifecycleScope.launch {
            exchangeCode(clientId, clientSecret, code, dataDir, store)
        }
    }

    private suspend fun exchangeCode(
        clientId: String,
        clientSecret: String,
        code: String,
        dataDir: String,
        store: TokenStore
    ) {
        withContext(Dispatchers.IO) {
            try {
                val python = Python.getInstance()
                val bridge = python.getModule("eufy_sync.android_bridge")
                bridge.callAttr("set_data_dir", dataDir)
                bridge.callAttr("exchange_strava_code", clientId, clientSecret, code)
                Log.i(TAG, "Strava tokens saved successfully")
                withContext(Dispatchers.Main) {
                    store.stravaAuthed = true
                    store.stravaCsrfState = null   // consume the one-time state
                    setResult(Activity.RESULT_OK)
                    finish()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Strava code exchange failed", e)
                withContext(Dispatchers.Main) {
                    setResult(Activity.RESULT_CANCELED)
                    finish()
                }
            }
        }
    }

    private fun failWith(msg: String) {
        Log.e(TAG, msg)
        setResult(Activity.RESULT_CANCELED)
        finish()
    }
}

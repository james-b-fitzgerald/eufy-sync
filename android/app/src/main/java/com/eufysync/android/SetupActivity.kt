package com.eufysync.android

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import android.net.Uri
import com.eufysync.android.auth.GarminAuthActivity
import com.eufysync.android.databinding.ActivitySetupBinding
import com.eufysync.android.storage.TokenStore
import com.eufysync.android.sync.SyncWorker
import java.security.SecureRandom
import java.util.Base64

/**
 * Multi-step setup wizard.
 *
 * Steps:
 * 1. Eufy credentials (email + password)
 * 2. Select sync targets (Garmin, Strava, or both)
 * 3. Garmin: enter credentials → open [GarminAuthActivity] for WebView SSO
 * 4. Strava: enter Client ID / Secret → open Chrome Custom Tab for OAuth
 * 5. Finish — saves everything to [TokenStore] and schedules [SyncWorker]
 *
 * The Strava OAuth redirect is handled by [auth.StravaCallbackActivity] which
 * is already registered in the manifest for `eufysync://strava/callback`.
 */
class SetupActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "SetupActivity"

        // Strava OAuth constants (same as strava_client.py)
        private const val STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
        private const val STRAVA_REDIRECT_URI = "eufysync://strava/callback"
        private const val STRAVA_SCOPE = "profile:write,profile:read_all"
    }

    private lateinit var binding: ActivitySetupBinding
    private lateinit var store: TokenStore

    // ── Activity result launchers ─────────────────────────────────────────────

    private val garminAuthLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            store.garminAuthed = true
            binding.textGarminAuthStatus.apply {
                text = getString(R.string.garmin_auth_success)
                visibility = View.VISIBLE
            }
            Log.i(TAG, "Garmin authentication succeeded")
        } else {
            binding.textGarminAuthStatus.apply {
                text = getString(R.string.garmin_auth_failed)
                visibility = View.VISIBLE
            }
            Log.w(TAG, "Garmin authentication failed/cancelled")
        }
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySetupBinding.inflate(layoutInflater)
        setContentView(binding.root)
        store = TokenStore(this)

        // Pre-fill from stored values
        binding.editEufyEmail.setText(store.eufyEmail ?: "")
        binding.editGarminEmail.setText(store.garminEmail ?: "")
        binding.checkGarmin.isChecked = store.syncGarmin
        binding.checkStrava.isChecked = store.syncStrava

        // Show/hide Strava section based on checkbox
        updateSectionVisibility()
        binding.checkGarmin.setOnCheckedChangeListener { _, _ -> updateSectionVisibility() }
        binding.checkStrava.setOnCheckedChangeListener { _, _ -> updateSectionVisibility() }

        binding.btnGarminLogin.setOnClickListener { launchGarminAuth() }
        binding.btnStravaAuthorize.setOnClickListener { launchStravaOAuth() }
        binding.btnFinish.setOnClickListener { finish() }
    }

    // ── Section visibility ────────────────────────────────────────────────────

    private fun updateSectionVisibility() {
        binding.sectionGarmin.visibility =
            if (binding.checkGarmin.isChecked) View.VISIBLE else View.GONE
        binding.sectionStrava.visibility =
            if (binding.checkStrava.isChecked) View.VISIBLE else View.GONE
    }

    // ── Garmin ────────────────────────────────────────────────────────────────

    private fun launchGarminAuth() {
        val email = binding.editGarminEmail.text?.toString()?.trim()

        // The user authenticates directly in the Garmin WebView — only the email
        // address (used as a display hint) is needed here; no password is stored.
        if (email.isNullOrBlank()) {
            Toast.makeText(this, getString(R.string.toast_garmin_email_required), Toast.LENGTH_SHORT).show()
            return
        }

        store.garminEmail = email

        val dataDir = "${filesDir.absolutePath}/.garmin-sync"
        val intent = Intent(this, GarminAuthActivity::class.java).apply {
            putExtra(GarminAuthActivity.EXTRA_DATA_DIR, dataDir)
        }
        garminAuthLauncher.launch(intent)
    }

    // ── Strava ────────────────────────────────────────────────────────────────

    private fun launchStravaOAuth() {
        val clientId     = binding.editStravaClientId.text?.toString()?.trim()
        val clientSecret = binding.editStravaClientSecret.text?.toString()?.trim()

        if (clientId.isNullOrBlank() || clientSecret.isNullOrBlank()) {
            Toast.makeText(this, getString(R.string.toast_strava_creds_required), Toast.LENGTH_SHORT).show()
            return
        }

        store.stravaClientId     = clientId
        store.stravaClientSecret = clientSecret

        // Generate and store a CSRF state value
        val state = generateCsrfState()
        store.stravaCsrfState = state

        val authUrl = Uri.parse(STRAVA_AUTH_URL).buildUpon()
            .appendQueryParameter("client_id", clientId)
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("redirect_uri", STRAVA_REDIRECT_URI)
            .appendQueryParameter("scope", STRAVA_SCOPE)
            .appendQueryParameter("approval_prompt", "force")
            .appendQueryParameter("state", state)
            .build()

        CustomTabsIntent.Builder().build()
            .launchUrl(this, authUrl)
    }

    /** Generate a URL-safe random state string for CSRF protection. */
    private fun generateCsrfState(): String {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
    }

    // ── Finish ────────────────────────────────────────────────────────────────

    override fun finish() {
        val eufy    = binding.editEufyEmail.text?.toString()?.trim()
        val eufyPw  = binding.editEufyPassword.text?.toString()
        val garmin  = binding.checkGarmin.isChecked
        val strava  = binding.checkStrava.isChecked

        if (eufy.isNullOrBlank() || eufyPw.isNullOrBlank()) {
            Toast.makeText(this, getString(R.string.toast_eufy_required), Toast.LENGTH_SHORT).show()
            return
        }
        if (!garmin && !strava) {
            Toast.makeText(this, getString(R.string.toast_select_target), Toast.LENGTH_SHORT).show()
            return
        }
        if (garmin && !store.garminAuthed) {
            Toast.makeText(this, getString(R.string.toast_garmin_auth_first), Toast.LENGTH_SHORT).show()
            return
        }
        if (strava && !store.stravaAuthed) {
            Toast.makeText(this, getString(R.string.toast_strava_auth_first), Toast.LENGTH_SHORT).show()
            return
        }

        store.eufyEmail    = eufy
        store.eufyPassword = eufyPw
        store.syncGarmin   = garmin
        store.syncStrava   = strava
        store.setupComplete = true

        // Schedule (or keep) the daily background sync
        if (store.autoSyncEnabled) SyncWorker.schedule(this)

        val targets = listOfNotNull(
            if (garmin) "Garmin" else null,
            if (strava) "Strava" else null
        ).joinToString(" + ")

        Toast.makeText(
            this,
            getString(R.string.setup_complete, targets),
            Toast.LENGTH_LONG
        ).show()

        setResult(Activity.RESULT_OK)
        super.finish()
    }
}

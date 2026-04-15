package com.eufysync.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.chaquo.python.Python
import com.eufysync.android.databinding.ActivityMainBinding
import com.eufysync.android.storage.TokenStore
import com.eufysync.android.sync.SyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Main / status screen.
 *
 * Shows:
 * - Last sync timestamp and summary
 * - Garmin and Strava token health (from `android_bridge.get_token_status()`)
 * - "Sync Now" button — triggers an immediate manual sync on the IO dispatcher
 * - "Configure" button — opens [SetupActivity]
 * - Auto-sync toggle — enables/disables the [SyncWorker] daily schedule
 *
 * On first launch (setup not complete) the activity immediately redirects to
 * [SetupActivity].
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var store: TokenStore

    private val setupLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { refreshStatus() }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* granted or denied — continue either way */ }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        store = TokenStore(this)

        requestNotificationPermissionIfNeeded()

        if (!store.setupComplete) {
            openSetup()
            return
        }

        binding.switchAutoSync.isChecked = store.autoSyncEnabled
        binding.switchAutoSync.setOnCheckedChangeListener { _, checked ->
            store.autoSyncEnabled = checked
            if (checked) SyncWorker.schedule(this) else SyncWorker.cancel(this)
        }

        binding.btnSyncNow.setOnClickListener { runManualSync() }
        binding.btnSetup.setOnClickListener { openSetup() }

        refreshStatus()
    }

    override fun onResume() {
        super.onResume()
        if (store.setupComplete) refreshStatus()
    }

    // ── Status refresh ────────────────────────────────────────────────────────

    private fun refreshStatus() {
        // Last sync summary from TokenStore (written by SyncWorker)
        val lastSync = store.lastSyncTime
        binding.textLastSync.text = if (lastSync != null) {
            val formatted = Instant.parse(lastSync)
                .atZone(ZoneId.systemDefault())
                .format(DateTimeFormatter.ofPattern("MMM d, h:mm a"))
            getString(R.string.status_last_sync, formatted)
        } else {
            getString(R.string.status_never_synced)
        }

        // Token health from Python (async to avoid blocking UI)
        lifecycleScope.launch {
            val statusJson = withContext(Dispatchers.IO) {
                try {
                    val python = Python.getInstance()
                    val bridge = python.getModule("eufy_sync.android_bridge")
                    val dataDir = "${filesDir.absolutePath}/.garmin-sync"
                    bridge.callAttr("get_token_status", dataDir).toString()
                } catch (e: Exception) {
                    null
                }
            }

            val status = if (statusJson != null) ParseUtils.parseTokenStatus(statusJson)
                         else emptyMap()

            binding.textGarminToken.text = getString(
                R.string.status_garmin_token,
                formatTokenState(status["garmin"])
            )
            binding.textStravaToken.text = getString(
                R.string.status_strava_token,
                formatTokenState(status["strava"])
            )
        }
    }

    private fun formatTokenState(tokenInfo: Map<String, Any>?): String {
        if (tokenInfo == null) return getString(R.string.token_no_session)
        return when (tokenInfo["state"] as? String) {
            "valid" -> {
                val days = (tokenInfo["days_remaining"] as? Double)?.toInt()
                if (days != null) getString(R.string.token_valid, days)
                else getString(R.string.token_valid, 0)
            }
            "refresh_needed" -> getString(R.string.token_refresh_needed)
            "expired"        -> getString(R.string.token_expired)
            "no_session"     -> getString(R.string.token_no_session)
            else             -> getString(R.string.token_error)
        }
    }

    // ── Manual sync ───────────────────────────────────────────────────────────

    private fun runManualSync() {
        if (!store.hasMinimumCredentials()) {
            openSetup()
            return
        }

        binding.btnSyncNow.isEnabled = false
        binding.textSyncStatus.apply {
            text = getString(R.string.syncing)
            visibility = View.VISIBLE
        }

        lifecycleScope.launch {
            val resultJson = withContext(Dispatchers.IO) {
                try {
                    val python = Python.getInstance()
                    val bridge = python.getModule("eufy_sync.android_bridge")
                    val dataDir = "${filesDir.absolutePath}/.garmin-sync"
                    bridge.callAttr(
                        "run_sync",
                        store.eufyEmail,
                        store.eufyPassword,
                        if (store.syncGarmin) store.garminEmail else null,
                        null,
                        if (store.syncStrava) store.stravaClientId else null,
                        if (store.syncStrava) store.stravaClientSecret else null,
                        dataDir,
                        null
                    ).toString()
                } catch (e: Exception) {
                    null
                }
            }

            binding.btnSyncNow.isEnabled = true

            val result = resultJson?.let { ParseUtils.parseSyncResult(it) }
            if (result != null && result.success) {
                val msg = getString(R.string.sync_success, result.totalSynced)
                store.lastSyncTime    = Instant.now().toString()
                store.lastSyncSummary = msg
                binding.textSyncStatus.text = msg
                Toast.makeText(this@MainActivity, msg, Toast.LENGTH_SHORT).show()
                refreshStatus()
            } else {
                val errorMsg = result?.error ?: "Unknown error"
                binding.textSyncStatus.text = errorMsg
                Toast.makeText(this@MainActivity, getString(R.string.sync_failed), Toast.LENGTH_LONG).show()
            }
        }
    }

    // ── Navigation ────────────────────────────────────────────────────────────

    private fun openSetup() {
        setupLauncher.launch(Intent(this, SetupActivity::class.java))
    }

    // ── Permissions ───────────────────────────────────────────────────────────

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}

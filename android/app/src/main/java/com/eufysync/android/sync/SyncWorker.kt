package com.eufysync.android.sync

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.chaquo.python.Python
import com.eufysync.android.EufySyncApplication.Companion.NOTIF_CHANNEL_ID
import com.eufysync.android.MainActivity
import com.eufysync.android.ParseUtils
import com.eufysync.android.R
import com.eufysync.android.storage.TokenStore
import java.time.Instant
import java.util.concurrent.TimeUnit

/**
 * WorkManager worker that runs the Eufy→Garmin/Strava sync once per day.
 *
 * Execution model:
 * - [WorkManager] schedules a [androidx.work.PeriodicWorkRequest] with a
 *   24-hour repeat interval and a [NetworkType.CONNECTED] constraint.
 * - The worker calls `android_bridge.run_sync()` (Python via Chaquopy) which
 *   runs [sync.sync_user][eufy_sync.sync.sync_user] unchanged with
 *   `headless=True` — if tokens are expired the worker fails fast and a
 *   notification prompts the user to re-authenticate.
 * - On success or failure a [NotificationCompat] is posted to the
 *   [NOTIF_CHANNEL_ID] channel created in [EufySyncApplication].
 *
 * Call [schedule] to enable and [cancel] to disable background sync.
 */
class SyncWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    companion object {
        private const val TAG = "SyncWorker"
        private const val WORK_NAME = "eufy_daily_sync"
        private const val NOTIF_ID = 1001

        /** Enqueue a once-per-day periodic work request (survives reboots). */
        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = PeriodicWorkRequestBuilder<SyncWorker>(24, TimeUnit.HOURS)
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(
                    WORK_NAME,
                    ExistingPeriodicWorkPolicy.KEEP,
                    request
                )
            Log.i(TAG, "Daily sync scheduled")
        }

        /** Cancel the periodic work request. */
        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
            Log.i(TAG, "Daily sync cancelled")
        }
    }

    override suspend fun doWork(): Result {
        val store = TokenStore(applicationContext)

        if (!store.hasMinimumCredentials()) {
            Log.w(TAG, "Credentials missing — skipping sync")
            notify(
                applicationContext.getString(R.string.notif_title_failure),
                applicationContext.getString(R.string.sync_failed)
            )
            return Result.failure()
        }

        val eufyEmail    = store.eufyEmail    ?: return failMissing("Eufy email")
        val eufyPassword = store.eufyPassword ?: return failMissing("Eufy password")

        val garminEmail    = if (store.syncGarmin) store.garminEmail    else null
        val garminPassword = if (store.syncGarmin) store.garminPassword else null
        val stravaClientId     = if (store.syncStrava) store.stravaClientId     else null
        val stravaClientSecret = if (store.syncStrava) store.stravaClientSecret else null

        val dataDir = "${applicationContext.filesDir.absolutePath}/.garmin-sync"

        Log.i(TAG, "Starting sync (garmin=${store.syncGarmin}, strava=${store.syncStrava})")

        return try {
            val python = Python.getInstance()
            val bridge = python.getModule("eufy_sync.android_bridge")

            val resultJson = bridge.callAttr(
                "run_sync",
                eufyEmail,
                eufyPassword,
                garminEmail,
                garminPassword,
                stravaClientId,
                stravaClientSecret,
                dataDir,
                null   // backfill_days — null means use watermark from state.db
            ).toString()

            val result = ParseUtils.parseSyncResult(resultJson)
            if (result == null || !result.success) {
                val errorMsg = result?.error ?: "Unknown error"
                Log.e(TAG, "Sync failed: $errorMsg")
                store.lastSyncSummary = "Failed: $errorMsg"
                notify(
                    applicationContext.getString(R.string.notif_title_failure),
                    applicationContext.getString(R.string.notif_body_failure, errorMsg)
                )
                Result.failure()
            } else {
                val summary = applicationContext.getString(
                    R.string.notif_body_success,
                    result.totalSynced,
                    result.targetsSummary.ifEmpty { "all targets" }
                )
                Log.i(TAG, "Sync succeeded: $summary")
                store.lastSyncTime    = Instant.now().toString()
                store.lastSyncSummary = summary
                notify(applicationContext.getString(R.string.notif_title_success), summary)
                Result.success()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Unexpected worker error", e)
            store.lastSyncSummary = "Error: ${e.message}"
            notify(
                applicationContext.getString(R.string.notif_title_failure),
                applicationContext.getString(R.string.notif_body_failure, e.message ?: "")
            )
            Result.failure()
        }
    }

    private fun failMissing(what: String): Result {
        Log.w(TAG, "$what missing in TokenStore")
        return Result.failure()
    }

    private fun notify(title: String, body: String) {
        val tapIntent = Intent(applicationContext, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        }
        val pendingIntent = PendingIntent.getActivity(
            applicationContext,
            0,
            tapIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(applicationContext, NOTIF_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_popup_sync)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        val nm = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE)
                as NotificationManager
        nm.notify(NOTIF_ID, notification)
    }
}

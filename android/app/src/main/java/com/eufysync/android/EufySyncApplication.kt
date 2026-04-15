package com.eufysync.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import com.chaquo.python.android.AndroidPlatform

/**
 * Application entry point.
 *
 * Initialises Chaquopy (CPython 3.12 runtime) and creates the notification
 * channel used by [sync.SyncWorker] to report background sync results.
 *
 * Chaquopy starts the Python interpreter once here so every subsequent call
 * to [com.chaquo.python.Python.getInstance] is free.  The eufy_sync package
 * is available on Python's path because the app's build.gradle adds the
 * repo root to Chaquopy's srcDirs.
 */
class EufySyncApplication : Application() {

    override fun onCreate() {
        super.onCreate()

        // Start Chaquopy — must happen before any Python.getInstance() call
        if (!com.chaquo.python.Python.isStarted()) {
            com.chaquo.python.Python.start(AndroidPlatform(this))
        }

        createNotificationChannel()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            NOTIF_CHANNEL_ID,
            getString(R.string.notif_channel_name),
            NotificationManager.IMPORTANCE_LOW
        )
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }

    companion object {
        const val NOTIF_CHANNEL_ID = "eufy_sync"
    }
}

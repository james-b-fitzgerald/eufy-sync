package com.eufysync.android.auth

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.util.Log
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.eufysync.android.ParseUtils
import com.eufysync.android.R
import com.eufysync.android.databinding.ActivityGarminAuthBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.lifecycle.lifecycleScope

/**
 * WebView-based Garmin SSO login activity.
 *
 * Mirrors [garmin_auth.browser_login()][eufy_sync.garmin_auth.browser_login]
 * on macOS: injects JavaScript that wraps `window.fetch` and `XMLHttpRequest`
 * to intercept the `/mobile/api/login` XHR response, extracts the
 * `serviceTicketId`, then delegates to `android_bridge.exchange_garmin_ticket()`
 * (pure httpx — no Playwright) to exchange it for DI OAuth2 tokens.
 *
 * Returns [Activity.RESULT_OK] on success, [Activity.RESULT_CANCELED] on failure.
 * The calling [SetupActivity] reads the result and updates [TokenStore.garminAuthed].
 */
class GarminAuthActivity : AppCompatActivity() {

    private lateinit var binding: ActivityGarminAuthBinding

    /** EXTRA key for the Garmin data-directory path passed in from [SetupActivity]. */
    companion object {
        const val EXTRA_DATA_DIR = "data_dir"
        private const val TAG = "GarminAuth"

        /**
         * Mobile Chrome user-agent string — mirrors the one used by
         * [garmin_auth.py][eufy_sync.garmin_auth] so the SSO page serves the
         * same Android-optimised login experience.
         *
         * Update this when the target Chrome / Android version is bumped.
         */
        private const val GARMIN_WEBVIEW_USER_AGENT =
            "Mozilla/5.0 (Linux; Android 13; sdk_gphone64_arm64) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/121.0.0.0 Mobile Safari/537.36"

        /**
         * Garmin mobile SSO URL — same as in garmin_auth.py [SSO_LOGIN_URL].
         * Opens the Android-styled Garmin login page.
         */
        private const val SSO_URL =
            "https://sso.garmin.com/mobile/sso/en_US/sign-in" +
            "?clientId=GCM_ANDROID_DARK" +
            "&service=https://mobile.integration.garmin.com/gcm/android"

        /**
         * Injected JavaScript — mirrors garmin_auth.py's `context.add_init_script(...)`.
         *
         * Wraps `window.fetch` and `XMLHttpRequest.send` to capture the login
         * response from `/mobile/api/login` and forward the JSON body to
         * [GarminCaptureInterface.onLoginCaptured] via the `GarminCapture`
         * JavaScript interface.
         */
        val INJECTION_SCRIPT = """
            (function() {
                if (window.__eufy_injected) return;
                window.__eufy_injected = true;

                const originalFetch = window.fetch;
                window.fetch = async function(...args) {
                    const response = await originalFetch.apply(this, args);
                    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                    if (url.includes('/mobile/api/login')) {
                        try {
                            const clone = response.clone();
                            const data = await clone.json();
                            window.GarminCapture.onLoginCaptured(JSON.stringify(data));
                        } catch(e) {}
                    }
                    return response;
                };

                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this._captureUrl = url;
                    return origOpen.call(this, method, url, ...rest);
                };
                XMLHttpRequest.prototype.send = function(...args) {
                    this.addEventListener('load', function() {
                        if (this._captureUrl && this._captureUrl.includes('/mobile/api/login')) {
                            try {
                                window.GarminCapture.onLoginCaptured(this.responseText);
                            } catch(e) {}
                        }
                    });
                    return origSend.apply(this, args);
                };
            })();
        """.trimIndent()
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityGarminAuthBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val dataDir = intent.getStringExtra(EXTRA_DATA_DIR)
        setupWebView(dataDir)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView(dataDir: String?) {
        val webView = binding.webViewGarmin
        val progress = binding.progressGarmin

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            userAgentString = GARMIN_WEBVIEW_USER_AGENT
        }

        // Register the JS interface before loading the URL
        webView.addJavascriptInterface(
            GarminCaptureInterface(dataDir),
            "GarminCapture"
        )

        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
                super.onPageStarted(view, url, favicon)
                progress.visibility = View.VISIBLE
            }

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                progress.visibility = View.GONE
                // Re-inject on every navigation (handles SPA redirects)
                view.evaluateJavascript(INJECTION_SCRIPT, null)
            }

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                // Only allow navigation within Garmin SSO domains.
                // Block non-Garmin or plaintext URLs to reduce the JS-interface attack surface.
                val host = request.url.host ?: return true
                val isGarminHost = host.endsWith("garmin.com") || host.endsWith("garmin.cn")
                val isHttps = request.url.scheme?.lowercase() == "https"
                return !(isGarminHost && isHttps)
            }
        }

        webView.webChromeClient = WebChromeClient()
        webView.loadUrl(SSO_URL)
    }

    /**
     * JavaScript interface exposed as `window.GarminCapture`.
     *
     * [onLoginCaptured] is called from the injected JS on the JS thread; it
     * hands off to a coroutine so the Python call (which blocks) runs on IO.
     */
    private inner class GarminCaptureInterface(private val dataDir: String?) {

        @JavascriptInterface
        fun onLoginCaptured(responseJson: String) {
            Log.d(TAG, "Login response intercepted (${responseJson.length} chars)")
            val ticket = ParseUtils.extractGarminTicket(responseJson) ?: run {
                Log.d(TAG, "Response is not a SUCCESSFUL login; waiting for next attempt")
                return
            }

            Log.i(TAG, "Service ticket captured; exchanging for OAuth2 tokens via Python bridge")
            lifecycleScope.launch {
                exchangeTicket(ticket, dataDir)
            }
        }
    }

    private suspend fun exchangeTicket(ticket: String, dataDir: String?) {
        withContext(Dispatchers.IO) {
            try {
                val python = Python.getInstance()
                val bridge = python.getModule("eufy_sync.android_bridge")
                if (dataDir != null) bridge.callAttr("set_data_dir", dataDir)
                bridge.callAttr("exchange_garmin_ticket", ticket)
                Log.i(TAG, "Garmin OAuth2 tokens saved successfully")
                withContext(Dispatchers.Main) {
                    setResult(Activity.RESULT_OK)
                    finish()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Ticket exchange failed", e)
                withContext(Dispatchers.Main) {
                    setResult(Activity.RESULT_CANCELED)
                    finish()
                }
            }
        }
    }
}

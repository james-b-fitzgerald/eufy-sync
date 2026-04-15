package com.eufysync.android

import com.google.gson.Gson
import com.google.gson.JsonSyntaxException
import com.google.gson.reflect.TypeToken
import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets

/**
 * Pure-Kotlin helpers for parsing results that cross the Chaquopy boundary.
 *
 * No Android API imports — every function here is testable with plain JUnit
 * without Robolectric or an emulator.
 */
object ParseUtils {

    private val gson = Gson()

    // -------------------------------------------------------------------------
    // Sync result — returned by android_bridge.run_sync()
    // -------------------------------------------------------------------------

    /** Result of a [sync.SyncWorker] cycle. */
    data class SyncResult(
        val success: Boolean,
        val counts: Map<String, Int>,
        val error: String?
    ) {
        /** Total number of measurements uploaded across all targets. */
        val totalSynced: Int get() = counts.values.sum()

        /** Comma-separated list of target names that received data. */
        val targetsSummary: String
            get() = counts.entries
                .filter { it.value > 0 }
                .joinToString(", ") { it.key.replaceFirstChar(Char::uppercaseChar) }
    }

    /**
     * Parse the JSON string returned by `android_bridge.run_sync()`.
     *
     * Returns null if the string is null, blank, or not valid JSON.
     */
    fun parseSyncResult(json: String?): SyncResult? {
        if (json.isNullOrBlank()) return null
        return try {
            val type = object : TypeToken<Map<String, Any>>() {}.type
            val map: Map<String, Any> = gson.fromJson(json, type)
            val success = (map["success"] as? Boolean) ?: false
            @Suppress("UNCHECKED_CAST")
            val counts = (map["counts"] as? Map<String, Double>)
                ?.mapValues { it.value.toInt() }
                ?: emptyMap()
            val error = map["error"] as? String
            SyncResult(success, counts, error)
        } catch (_: JsonSyntaxException) {
            null
        }
    }

    // -------------------------------------------------------------------------
    // Garmin login capture — injected JS calls GarminCapture.onLoginCaptured()
    // -------------------------------------------------------------------------

    /**
     * Extract the Garmin `serviceTicketId` from the SSO login XHR response JSON.
     *
     * Expected shape (mirrors what garmin_auth.py intercepts in Playwright):
     * ```json
     * { "responseStatus": { "type": "SUCCESSFUL" }, "serviceTicketId": "ST-…" }
     * ```
     * Returns null if the JSON is unparseable, the status is not SUCCESSFUL, or
     * the ticket field is absent / empty.
     */
    fun extractGarminTicket(responseJson: String): String? {
        return try {
            val type = object : TypeToken<Map<String, Any>>() {}.type
            val map: Map<String, Any> = gson.fromJson(responseJson, type)

            @Suppress("UNCHECKED_CAST")
            val statusType = (map["responseStatus"] as? Map<String, Any>)
                ?.get("type") as? String
            if (statusType != "SUCCESSFUL") return null

            (map["serviceTicketId"] as? String)?.takeIf { it.isNotEmpty() }
        } catch (_: Exception) {
            null
        }
    }

    // -------------------------------------------------------------------------
    // Strava callback URI — eufysync://strava/callback?code=…&state=…
    // -------------------------------------------------------------------------

    /**
     * Parse the query string from a Strava OAuth callback URI.
     *
     * @param uriString The full callback URI string.
     * @param expectedState CSRF state value that must match the `state` param.
     * @return The `code` if the state matches; null otherwise.
     */
    fun extractStravaCode(uriString: String, expectedState: String): String? {
        return try {
            val query = URI(uriString).query ?: return null
            val params = parseQueryString(query)
            if (params["state"] != expectedState) return null
            params["code"]?.takeIf { it.isNotEmpty() }
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Split a URL query string (no leading `?`) into a key→value map.
     * Duplicate keys retain the last value.  Both keys and values are
     * percent-decoded using UTF-8 via [URLDecoder].
     */
    fun parseQueryString(query: String): Map<String, String> {
        if (query.isBlank()) return emptyMap()
        return query.split("&")
            .mapNotNull { pair ->
                val idx = pair.indexOf('=')
                if (idx < 1) null
                else {
                    val key = URLDecoder.decode(pair.substring(0, idx), StandardCharsets.UTF_8.name())
                    val value = URLDecoder.decode(pair.substring(idx + 1), StandardCharsets.UTF_8.name())
                    key to value
                }
            }
            .toMap()
    }

    // -------------------------------------------------------------------------
    // Token status — returned by android_bridge.get_token_status()
    // -------------------------------------------------------------------------

    /**
     * Parse the JSON returned by `android_bridge.get_token_status()`.
     *
     * Returns a map of service name → status map, e.g.:
     * `{"garmin": {"state": "valid", "days_remaining": 200}, "strava": {...}}`
     */
    @Suppress("UNCHECKED_CAST")
    fun parseTokenStatus(json: String?): Map<String, Map<String, Any>> {
        if (json.isNullOrBlank()) return emptyMap()
        return try {
            val type = object : TypeToken<Map<String, Map<String, Any>>>() {}.type
            gson.fromJson(json, type) ?: emptyMap()
        } catch (_: JsonSyntaxException) {
            emptyMap()
        }
    }
}

package com.eufysync.android

import com.eufysync.android.ParseUtils.SyncResult
import org.junit.Assert.*
import org.junit.Test

/**
 * JUnit unit tests for [ParseUtils].
 *
 * All helpers in ParseUtils are pure Kotlin with no Android API dependencies,
 * so these run directly on the JVM without Robolectric or an emulator.
 *
 * Covers:
 *   - [ParseUtils.parseSyncResult]
 *   - [ParseUtils.extractGarminTicket]
 *   - [ParseUtils.extractStravaCode]
 *   - [ParseUtils.parseQueryString]
 *   - [ParseUtils.parseTokenStatus]
 *   - [SyncResult] computed properties
 */
class ParseUtilsTest {

    // =========================================================================
    // parseSyncResult
    // =========================================================================

    @Test
    fun `parseSyncResult - success with counts`() {
        val json = """{"success": true, "counts": {"garmin": 3, "strava": 1}}"""
        val result = ParseUtils.parseSyncResult(json)
        assertNotNull(result)
        assertTrue(result!!.success)
        assertEquals(3, result.counts["garmin"])
        assertEquals(1, result.counts["strava"])
        assertNull(result.error)
    }

    @Test
    fun `parseSyncResult - failure with error message`() {
        val json = """{"success": false, "error": "token expired"}"""
        val result = ParseUtils.parseSyncResult(json)
        assertNotNull(result)
        assertFalse(result!!.success)
        assertEquals("token expired", result.error)
        assertTrue(result.counts.isEmpty())
    }

    @Test
    fun `parseSyncResult - success garmin only`() {
        val json = """{"success": true, "counts": {"garmin": 5}}"""
        val result = ParseUtils.parseSyncResult(json)!!
        assertEquals(5, result.counts["garmin"])
        assertNull(result.counts["strava"])
    }

    @Test
    fun `parseSyncResult - empty counts`() {
        val json = """{"success": true, "counts": {}}"""
        val result = ParseUtils.parseSyncResult(json)!!
        assertTrue(result.counts.isEmpty())
        assertEquals(0, result.totalSynced)
    }

    @Test
    fun `parseSyncResult - null input returns null`() {
        assertNull(ParseUtils.parseSyncResult(null))
    }

    @Test
    fun `parseSyncResult - blank string returns null`() {
        assertNull(ParseUtils.parseSyncResult("   "))
    }

    @Test
    fun `parseSyncResult - invalid JSON returns null`() {
        assertNull(ParseUtils.parseSyncResult("{not valid json"))
    }

    // =========================================================================
    // SyncResult computed properties
    // =========================================================================

    @Test
    fun `SyncResult totalSynced sums all counts`() {
        val r = SyncResult(true, mapOf("garmin" to 3, "strava" to 2), null)
        assertEquals(5, r.totalSynced)
    }

    @Test
    fun `SyncResult totalSynced is zero when no counts`() {
        val r = SyncResult(true, emptyMap(), null)
        assertEquals(0, r.totalSynced)
    }

    @Test
    fun `SyncResult targetsSummary skips zero-count targets`() {
        val r = SyncResult(true, mapOf("garmin" to 3, "strava" to 0), null)
        assertEquals("Garmin", r.targetsSummary)
    }

    @Test
    fun `SyncResult targetsSummary is empty when all counts are zero`() {
        val r = SyncResult(true, mapOf("garmin" to 0), null)
        assertTrue(r.targetsSummary.isEmpty())
    }

    @Test
    fun `SyncResult targetsSummary capitalises service names`() {
        val r = SyncResult(true, mapOf("garmin" to 1, "strava" to 1), null)
        val summary = r.targetsSummary
        assertTrue(summary.contains("Garmin"))
        assertTrue(summary.contains("Strava"))
    }

    // =========================================================================
    // extractGarminTicket
    // =========================================================================

    @Test
    fun `extractGarminTicket - returns ticket on SUCCESSFUL response`() {
        val json = """
            {
              "responseStatus": {"type": "SUCCESSFUL"},
              "serviceTicketId": "ST-abc123"
            }
        """.trimIndent()
        assertEquals("ST-abc123", ParseUtils.extractGarminTicket(json))
    }

    @Test
    fun `extractGarminTicket - returns null for non-SUCCESSFUL status`() {
        val json = """
            {
              "responseStatus": {"type": "FAILED"},
              "serviceTicketId": "ST-abc123"
            }
        """.trimIndent()
        assertNull(ParseUtils.extractGarminTicket(json))
    }

    @Test
    fun `extractGarminTicket - returns null when ticket field is absent`() {
        val json = """{"responseStatus": {"type": "SUCCESSFUL"}}"""
        assertNull(ParseUtils.extractGarminTicket(json))
    }

    @Test
    fun `extractGarminTicket - returns null when ticket is empty string`() {
        val json = """{"responseStatus": {"type": "SUCCESSFUL"}, "serviceTicketId": ""}"""
        assertNull(ParseUtils.extractGarminTicket(json))
    }

    @Test
    fun `extractGarminTicket - returns null for invalid JSON`() {
        assertNull(ParseUtils.extractGarminTicket("{bad json"))
    }

    @Test
    fun `extractGarminTicket - returns null for empty string`() {
        assertNull(ParseUtils.extractGarminTicket(""))
    }

    @Test
    fun `extractGarminTicket - returns null when responseStatus is missing`() {
        val json = """{"serviceTicketId": "ST-abc"}"""
        assertNull(ParseUtils.extractGarminTicket(json))
    }

    // =========================================================================
    // parseQueryString
    // =========================================================================

    @Test
    fun `parseQueryString - parses simple key-value pairs`() {
        val result = ParseUtils.parseQueryString("code=ABC&state=XYZ")
        assertEquals("ABC", result["code"])
        assertEquals("XYZ", result["state"])
    }

    @Test
    fun `parseQueryString - returns empty map for blank input`() {
        assertTrue(ParseUtils.parseQueryString("").isEmpty())
        assertTrue(ParseUtils.parseQueryString("   ").isEmpty())
    }

    @Test
    fun `parseQueryString - handles single param`() {
        val result = ParseUtils.parseQueryString("code=abc123")
        assertEquals("abc123", result["code"])
    }

    @Test
    fun `parseQueryString - last value wins for duplicate keys`() {
        val result = ParseUtils.parseQueryString("k=first&k=second")
        assertEquals("second", result["k"])
    }

    @Test
    fun `parseQueryString - ignores params without equals sign`() {
        val result = ParseUtils.parseQueryString("novalue&code=abc")
        assertEquals("abc", result["code"])
        assertNull(result["novalue"])
    }

    @Test
    fun `parseQueryString - allows equals sign in value`() {
        val result = ParseUtils.parseQueryString("token=a=b=c")
        assertEquals("a=b=c", result["token"])
    }

    // =========================================================================
    // extractStravaCode
    // =========================================================================

    @Test
    fun `extractStravaCode - returns code when state matches`() {
        val uri = "eufysync://strava/callback?code=auth-code-xyz&state=mystate"
        val code = ParseUtils.extractStravaCode(uri, "mystate")
        assertEquals("auth-code-xyz", code)
    }

    @Test
    fun `extractStravaCode - returns null when state does not match`() {
        val uri = "eufysync://strava/callback?code=auth-code&state=wrongstate"
        assertNull(ParseUtils.extractStravaCode(uri, "expectedstate"))
    }

    @Test
    fun `extractStravaCode - returns null when code is absent`() {
        val uri = "eufysync://strava/callback?state=mystate"
        assertNull(ParseUtils.extractStravaCode(uri, "mystate"))
    }

    @Test
    fun `extractStravaCode - returns null when code is empty`() {
        val uri = "eufysync://strava/callback?code=&state=mystate"
        assertNull(ParseUtils.extractStravaCode(uri, "mystate"))
    }

    @Test
    fun `extractStravaCode - returns null for malformed URI`() {
        assertNull(ParseUtils.extractStravaCode("not a uri :-)", "state"))
    }

    @Test
    fun `extractStravaCode - handles URI without query string`() {
        assertNull(ParseUtils.extractStravaCode("eufysync://strava/callback", "state"))
    }

    // =========================================================================
    // parseTokenStatus
    // =========================================================================

    @Test
    fun `parseTokenStatus - parses garmin and strava entries`() {
        val json = """
            {
              "garmin": {"state": "valid", "days_remaining": 200},
              "strava": {"state": "no_session", "days_remaining": null}
            }
        """.trimIndent()
        val status = ParseUtils.parseTokenStatus(json)
        assertEquals("valid", status["garmin"]?.get("state"))
        assertEquals("no_session", status["strava"]?.get("state"))
    }

    @Test
    fun `parseTokenStatus - parses days_remaining as Double`() {
        val json = """{"garmin": {"state": "valid", "days_remaining": 150}}"""
        val status = ParseUtils.parseTokenStatus(json)
        val days = (status["garmin"]?.get("days_remaining") as? Double)?.toInt()
        assertEquals(150, days)
    }

    @Test
    fun `parseTokenStatus - returns empty map for null input`() {
        assertTrue(ParseUtils.parseTokenStatus(null).isEmpty())
    }

    @Test
    fun `parseTokenStatus - returns empty map for blank string`() {
        assertTrue(ParseUtils.parseTokenStatus("  ").isEmpty())
    }

    @Test
    fun `parseTokenStatus - returns empty map for invalid JSON`() {
        assertTrue(ParseUtils.parseTokenStatus("not json").isEmpty())
    }

    @Test
    fun `parseTokenStatus - partial result only has garmin`() {
        val json = """{"garmin": {"state": "expired", "days_remaining": 0}}"""
        val status = ParseUtils.parseTokenStatus(json)
        assertNotNull(status["garmin"])
        assertNull(status["strava"])
    }
}

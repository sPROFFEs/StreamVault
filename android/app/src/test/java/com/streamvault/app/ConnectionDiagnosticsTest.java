package com.streamvault.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class ConnectionDiagnosticsTest {
    @Test public void reportsOriginsAndKnownRouteKindsButNeverCredentialsOrPaths() {
        ConnectionDiagnostics log = new ConnectionDiagnostics();
        log.add("navigation blocked", "https://user:SecretPassword@team.cloudflareaccess.com/cdn-cgi/access/login/PrivateTitle?code=123456&token=SecretToken#SecretFragment", 0);
        log.add("http error", "https://watch.example.com/private/SecretTitle?jwt=SecretJwt", 403);
        String report = log.report();
        assertTrue(report.contains("https://team.cloudflareaccess.com"));
        assertTrue(report.contains("access-login"));
        assertTrue(report.contains("403"));
        for (String secret : new String[]{"Secret", "123456", "user:", "PrivateTitle", "?", "#"}) {
            assertFalse(secret, report.contains(secret));
        }
    }

    @Test public void keepsOnlyBoundedRecentEventsAndRejectsMalformedUrls() {
        ConnectionDiagnostics log = new ConnectionDiagnostics();
        log.add("first", "file:///private/SecretFile", 0);
        assertFalse(log.report().contains("SecretFile"));
        log.add("bad", "https://user:Secret@broken host", 0);
        assertFalse(log.report().contains("Secret"));
        for (int i = 0; i < 50; i++) log.add("event-" + i, "https://watch.example.com/", 0);
        assertFalse(log.report().contains("first"));
        assertTrue(log.report().contains("event-49"));
        assertEquals(32, log.report().lines().count());
    }

    @Test public void browserErrorsKeepOnlyCategoriesNotMessagesOrSecrets() {
        ConnectionDiagnostics log = new ConnectionDiagnostics();
        log.browserError("https://team.cloudflareaccess.com/private?code=123456", "CORS blocked SecretPassword and code 123456");
        log.browserError("https://challenges.cloudflare.com/secret", "Turnstile failed with SecretToken");
        assertTrue(log.report().contains("cross-origin browser error"));
        assertTrue(log.report().contains("challenge browser error"));
        assertFalse(log.report().contains("123456"));
        assertFalse(log.report().contains("Secret"));
        assertFalse(log.report().contains("private"));
    }
}

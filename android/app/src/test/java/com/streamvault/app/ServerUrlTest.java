package com.streamvault.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class ServerUrlTest {
    @Test public void recoveryKeepsOnlySelectedServerLocations() {
        String origin = "https://watch.example.com/";
        assertEquals(origin + "browse?type=tv", ServerUrl.recoveryUrl(origin, origin + "browse?type=tv"));
        assertEquals(origin, ServerUrl.recoveryUrl(origin, "https://other.example/browse"));
        assertEquals(origin, ServerUrl.recoveryUrl(origin, "about:blank"));
        assertEquals(origin, ServerUrl.recoveryUrl(origin, null));
    }

    @Test public void normalizesHttpsOrigins() {
        assertEquals("https://watch.example.com/", ServerUrl.normalize(" HTTPS://Watch.Example.com ", false));
        assertEquals("https://watch.example.com:8443/", ServerUrl.normalize("https://watch.example.com:8443/", false));
    }

    @Test public void rejectsSecretsPathsAndNonWebSchemes() {
        for (String url : new String[]{"https://user:secret@example.com", "https://example.com/login",
                "https://example.com?token=secret", "https://example.com/#token", "file:///tmp/a",
                "javascript:alert(1)", "https://", "https://example.com:99999", "https://example.com\\evil"}) {
            assertRejected(url, true);
        }
    }

    @Test public void debugHttpIsLimitedToDevelopmentLoopback() {
        for (String host : new String[]{"10.0.2.2", "127.0.0.1", "localhost"}) {
            String url = "http://" + host + ":8000/";
            assertEquals(url, ServerUrl.normalize(url, true));
            assertRejected(url, false);
        }
        assertRejected("http://watch.example.com/", true);
        assertRejected("http://10.0.2.2.evil.test/", true);
        assertRejected("http://192.168.1.10/", true);
    }

    @Test public void navigationRequiresTheExactOrigin() {
        String origin = "https://watch.example.com/";
        assertTrue(ServerUrl.sameOrigin(origin, "https://WATCH.example.com:443/login?next=/portal"));
        for (String url : new String[]{"https://watch.example.com.evil.test/", "http://watch.example.com/",
                "https://watch.example.com:8443/", "https://user@watch.example.com/", "javascript:alert(1)", "bad url"}) {
            assertFalse(url, ServerUrl.sameOrigin(origin, url));
        }
    }

    @Test public void recognizesCloudflareAuthHosts() {
        assertTrue(ServerUrl.isCloudflareAuth("https://myteam.cloudflareaccess.com/cdn-cgi/access/login"));
        assertTrue(ServerUrl.isCloudflareAuth("https://cloudflareaccess.com/auth"));
        assertTrue(ServerUrl.isCloudflareAuth("https://challenges.cloudflare.com/turnstile/v0/api.js"));
        assertTrue(ServerUrl.isCloudflareAuth("https://auth.cloudflare.com/cdn-cgi/access/callback"));
        assertFalse(ServerUrl.isCloudflareAuth("https://watch.example.com/login"));
        assertFalse(ServerUrl.isCloudflareAuth("https://cloudflare.com.evil.test/"));
        assertFalse(ServerUrl.isCloudflareAuth(null));
        assertFalse(ServerUrl.isCloudflareAuth("not-a-valid-url"));
    }

    private static void assertRejected(String url, boolean debug) {
        assertThrows(url, IllegalArgumentException.class, () -> ServerUrl.normalize(url, debug));
    }
}

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

    @Test public void challengeFramesAllowBlankAndSrcdocWithoutTopLevelNavigation() {
        String origin = "https://watch.example.com/";
        for (String url : new String[]{"about:blank", "about:srcdoc", "about:blank#challenge"}) {
            assertTrue(url, ServerUrl.isAllowedSubframe(url));
            assertFalse(url, ServerUrl.sameOrigin(origin, url));
            assertFalse(url, ServerUrl.isCloudflareAuth(url));
        }
    }

    @Test public void challengeFramesKeepOtherNonWebSchemesBlocked() {
        assertTrue(ServerUrl.isAllowedSubframe("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/"));
        assertTrue(ServerUrl.isAllowedSubframe("http://provider.example/video"));
        for (String url : new String[]{"file:///data/data/com.streamvault.app/", "content://private/data",
                "javascript:alert(1)", "intent://external", "about:settings", "about:blank.evil",
                "https://", "https://user:password@provider.example/", "not a URL"}) {
            assertFalse(url, ServerUrl.isAllowedSubframe(url));
        }
        assertFalse(ServerUrl.isAllowedSubframe(null));
    }

    @Test public void browserOwnedChallengeFramesStayInsideWebView() {
        for (String url : new String[]{"data:text/html,%3Cp%3Everifying%3C/p%3E",
                "blob:https://challenges.cloudflare.com/73d60ed4", "blob:null/73d60ed4"}) {
            assertTrue(url, ServerUrl.isAllowedSubframe(url));
            assertFalse(url, ServerUrl.isAllowedMainFrame("https://watch.example.com/", url, true, false));
        }
        assertFalse(ServerUrl.isAllowedSubframe("blob:file:///private/challenge"));
        assertFalse(ServerUrl.isAllowedSubframe("blob:content://private/challenge"));
    }

    @Test public void accessRedirectChainDoesNotDependOnCloudflareHostnameAllowlist() {
        String origin = "https://watch.example.com/";
        for (String target : new String[]{origin, "https://team.cloudflareaccess.com/cdn-cgi/access/login",
                "https://identity.example.org/sign-in", "https://identity.example.org/verify",
                "https://custom-access.example.net/callback", origin + "cdn-cgi/access/authorized", origin + "portal"}) {
            assertTrue(target, ServerUrl.isAllowedMainFrame(origin, target, true, false));
            assertTrue(target, ServerUrl.isAllowedMainFrame(origin, target, true, true));
        }
        // Outside authentication, user-activated external links still open the browser.
        assertFalse(ServerUrl.isAllowedMainFrame(origin, "https://external.example.org/", false, true));
        assertTrue(ServerUrl.isAllowedMainFrame(origin, "https://identity.example.org/redirect", false, false));
        assertTrue(ServerUrl.isAllowedMainFrame(origin, origin + "media/1", false, true));
    }

    @Test public void authenticationDoesNotPermitUnsafeTopLevelNavigation() {
        String origin = "https://watch.example.com/";
        for (String target : new String[]{"http://identity.example.org/", "https://user:secret@identity.example.org/",
                "file:///private", "content://private", "javascript:alert(1)", "intent://login",
                "about:blank", "https://identity.example.org:99999/", "bad URL"}) {
            assertFalse(target, ServerUrl.isAllowedMainFrame(origin, target, true, false));
        }
        assertFalse(ServerUrl.isAllowedMainFrame(origin, null, true, false));
    }

    @Test public void accessFlowIncludesTheReturnCallbackButNotRegularAppPages() {
        String origin = "https://watch.example.com/";
        assertTrue(ServerUrl.isAccessPage(origin, "https://team.cloudflareaccess.com/cdn-cgi/access/login"));
        assertTrue(ServerUrl.isAccessPage(origin, origin + "cdn-cgi/access/authorized?token=example"));
        assertFalse(ServerUrl.isAccessPage(origin, origin + "portal"));
        assertFalse(ServerUrl.isAccessPage(origin, origin + "login"));
        assertFalse(ServerUrl.isAccessPage(origin, null));
    }

    @Test public void cloudflareAuthenticationRequiresHttpsWithoutCredentials() {
        for (String url : new String[]{"http://myteam.cloudflareaccess.com/cdn-cgi/access/login",
                "ftp://challenges.cloudflare.com/", "https://user:password@myteam.cloudflareaccess.com/",
                "https://myteam.cloudflareaccess.com:8443/", "https://evilcloudflareaccess.com/",
                "https://challenges.cloudflare.com.evil.test/"}) {
            assertFalse(url, ServerUrl.isCloudflareAuth(url));
        }
    }

    private static void assertRejected(String url, boolean debug) {
        assertThrows(url, IllegalArgumentException.class, () -> ServerUrl.normalize(url, debug));
    }
}

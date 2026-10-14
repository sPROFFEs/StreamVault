package com.streamvault.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class WebViewVersionTest {
    @Test public void requiresTheFeaturesUsedByTheHostedShell() {
        assertFalse(WebViewVersion.supportsApp("69.0.3497.100"));
        assertFalse(WebViewVersion.supportsApp("107.0.0.0"));
        assertTrue(WebViewVersion.supportsApp("108.0.0.0"));
        assertTrue(WebViewVersion.supportsApp("138.0.7204.181"));
    }
    @Test public void rejectsUnavailableOrMalformedProviders() {
        assertFalse(WebViewVersion.supportsApp(null));
        assertFalse(WebViewVersion.supportsApp(""));
        assertFalse(WebViewVersion.supportsApp("unknown"));
    }
}

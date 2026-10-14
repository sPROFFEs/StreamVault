package com.streamvault.app;

/** The hosted shell uses Array.at, inert and dynamic viewport units. */
final class WebViewVersion {
    private WebViewVersion() {}

    static boolean supportsApp(String version) {
        if (version == null) return false;
        try { return Integer.parseInt(version.split("\\.", 2)[0]) >= 108; }
        catch (NumberFormatException exception) { return false; }
    }
}

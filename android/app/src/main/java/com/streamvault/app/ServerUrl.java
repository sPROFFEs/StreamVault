package com.streamvault.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Origin-only server settings keep credentials out of preferences. */
final class ServerUrl {
    private ServerUrl() {}

    static String normalize(String value, boolean debug) {
        try {
            URI uri = new URI(value.trim());
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
            String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
            boolean developmentHost = host.equals("10.0.2.2") || host.equals("127.0.0.1") || host.equals("localhost");
            if (host.isEmpty() || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                    || uri.getRawFragment() != null || uri.getPort() > 65535 || uri.getPort() == 0
                    || !(uri.getRawPath().isEmpty() || uri.getRawPath().equals("/"))
                    || !(scheme.equals("https") || (debug && scheme.equals("http") && developmentHost))) {
                throw new IllegalArgumentException("Enter a server origin using HTTPS.");
            }
            return new URI(scheme, null, host, uri.getPort(), "/", null, null).toString();
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("Invalid server URL.", exception);
        }
    }

    static String recoveryUrl(String origin, String lastUrl) {
        return lastUrl != null && sameOrigin(origin, lastUrl) ? lastUrl : origin;
    }

    static boolean sameOrigin(String origin, String target) {
        try {
            URI base = new URI(origin);
            URI next = new URI(target);
            return next.getRawUserInfo() == null && base.getHost() != null && next.getHost() != null
                    && base.getScheme().equalsIgnoreCase(next.getScheme())
                    && base.getHost().equalsIgnoreCase(next.getHost()) && port(base) == port(next);
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    static boolean isCloudflareAuth(String url) {
        if (url == null) return false;
        try {
            URI uri = new URI(url);
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getRawUserInfo() != null
                    || (uri.getPort() != -1 && uri.getPort() != 443)) return false;
            String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
            String path = uri.getPath() == null ? "" : uri.getPath().toLowerCase(Locale.ROOT);
            return host.endsWith(".cloudflareaccess.com")
                    || host.equals("cloudflareaccess.com")
                    || host.equals("challenges.cloudflare.com")
                    || (host.endsWith(".cloudflare.com") && path.startsWith("/cdn-cgi/"));
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    static boolean isAccessPage(String origin, String url) {
        if (isCloudflareAuth(url)) return true;
        if (url == null || !sameOrigin(origin, url)) return false;
        try {
            return new URI(url).getPath().startsWith("/cdn-cgi/access/");
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    static boolean isAllowedMainFrame(String origin, String target, boolean authenticating, boolean userGesture) {
        if (target == null) return false;
        if (sameOrigin(origin, target) || isCloudflareAuth(target)) return true;
        // Like a browser, let HTTPS redirects complete in the original cookie
        // store. Access may use custom domains or an external identity provider.
        // Outside authentication, explicit external links still open the browser.
        return (authenticating || !userGesture) && isHttpsPage(target);
    }

    private static boolean isHttpsPage(String url) {
        try {
            URI uri = new URI(url);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null
                    && uri.getRawUserInfo() == null && uri.getPort() != 0 && uri.getPort() <= 65535;
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    static boolean isAllowedSubframe(String url) {
        if (url == null) return false;
        try {
            URI uri = new URI(url);
            // Browser-owned challenge/player frames must not be treated as
            // external app links. File/content access stays disabled in WebView.
            if ("about".equalsIgnoreCase(uri.getScheme())) {
                return "blank".equals(uri.getRawSchemeSpecificPart())
                        || "srcdoc".equals(uri.getRawSchemeSpecificPart());
            }
            if ("data".equalsIgnoreCase(uri.getScheme())) return !uri.getRawSchemeSpecificPart().isEmpty();
            if ("blob".equalsIgnoreCase(uri.getScheme())) {
                String source = uri.getRawSchemeSpecificPart();
                return source.startsWith("null/") || isHttpsPage(source)
                        || (source.startsWith("http://") && isAllowedSubframe(source));
            }
            return uri.getHost() != null && uri.getRawUserInfo() == null
                    && ("https".equalsIgnoreCase(uri.getScheme()) || "http".equalsIgnoreCase(uri.getScheme()));
        } catch (URISyntaxException exception) {
            return false;
        }
    }

    private static int port(URI uri) {
        return uri.getPort() == -1 ? (uri.getScheme().equalsIgnoreCase("https") ? 443 : 80) : uri.getPort();
    }
}

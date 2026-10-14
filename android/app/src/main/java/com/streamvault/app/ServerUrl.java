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

    private static int port(URI uri) {
        return uri.getPort() == -1 ? (uri.getScheme().equalsIgnoreCase("https") ? 443 : 80) : uri.getPort();
    }
}

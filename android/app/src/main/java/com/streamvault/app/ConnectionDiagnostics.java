package com.streamvault.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayDeque;
import java.util.Locale;

/** Memory-only events. Never record bodies, headers, query strings or full paths. */
final class ConnectionDiagnostics {
    private final ArrayDeque<String> events = new ArrayDeque<>();
    private final long started = System.nanoTime();

    // Event labels are app constants, never console messages or server error text.
    void add(String event, String url, int code) {
        if (events.size() == 32) events.removeFirst();
        events.addLast((System.nanoTime() - started) / 1_000_000 + "ms " + event + " "
                + location(url) + (code == 0 ? "" : " code=" + code));
    }

    String report() { return String.join("\n", events); }

    void browserError(String source, String message) {
        String text = message == null ? "" : message.toLowerCase(Locale.ROOT);
        String category = text.contains("cors") || text.contains("access-control-allow-origin")
                ? "cross-origin browser error"
                : text.contains("turnstile") || text.contains("cloudflare") ? "challenge browser error"
                : text.contains("content security policy") ? "content-policy browser error"
                : text.contains("mixed content") ? "mixed-content browser error"
                : text.contains("syntaxerror") ? "javascript syntax error"
                : text.contains("typeerror") ? "javascript type error" : "browser error";
        add(category, source, 0);
    }

    private static String location(String value) {
        if (value == null) return "unknown";
        try {
            URI uri = new URI(value);
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
            if (!(scheme.equals("https") || scheme.equals("http"))) return "non-web";
            if (uri.getHost() == null) return "invalid-url";
            String origin = new URI(scheme, null, uri.getHost(), uri.getPort(), null, null, null).toString();
            String path = uri.getPath() == null ? "" : uri.getPath();
            String kind = path.startsWith("/cdn-cgi/access/login") ? "access-login"
                    : path.startsWith("/cdn-cgi/access/callback") ? "access-callback"
                    : path.startsWith("/cdn-cgi/challenge-platform/") ? "challenge" : "page";
            return origin + " " + kind;
        } catch (URISyntaxException ignored) {
            return "invalid-url";
        }
    }
}

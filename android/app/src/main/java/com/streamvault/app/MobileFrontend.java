package com.streamvault.app;

import android.app.ActivityManager;
import android.content.Context;
import android.net.Uri;
import android.webkit.WebResourceResponse;
import android.webkit.ServiceWorkerClient;
import android.webkit.WebResourceRequest;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URLConnection;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/** Public installed assets only, at the exact configured origin (including scheme and port). */
final class MobileFrontend {
    private final Context context;
    private final String server;
    private final Set<String> files = new HashSet<>();

    MobileFrontend(Context context, String server) {
        this.context = context.getApplicationContext();
        this.server = server;
        try {
            JSONObject manifest = new JSONObject(read("manifest.json"));
            manifest.getJSONObject("files").keys().forEachRemaining(files::add);
        } catch (Exception error) { throw new IllegalStateException("Installed interface unavailable", error); }
    }

    WebResourceResponse intercept(Uri uri, String method, boolean mainFrame) {
        if (!"GET".equals(method) || !ServerUrl.sameOrigin(server, uri.toString())) return null;
        String path = uri.getPath();
        if (path == null || path.contains("\\") || path.indexOf('\0') >= 0) return null;
        for (String part : path.split("/")) if (part.equals(".") || part.equals("..")) return null;
        if (path.equals("/about") && !"1".equals(uri.getQueryParameter("hosted"))) {
            // Read only the UI-language preference; no cookie is copied or sent to GitHub.
            String cookies = android.webkit.CookieManager.getInstance().getCookie(server);
            boolean spanish = cookies != null && java.util.regex.Pattern.compile("(^|;)\\s*lang=es(?:;|$)").matcher(cookies).find();
            return asset("pages/" + (spanish ? "es" : "en") + "-user-about.html");
        }
        // A fetch made by an already installed SW may not carry the main-frame flag.
        // Only these UI routes are intercepted; APIs, authentication and player URLs are not.
        if (!"1".equals(uri.getQueryParameter("hosted")) &&
                (Arrays.asList("/portal", "/browse", "/downloads", "/iptv", "/anime", "/youtube", "/about").contains(path)
                || path.matches("/media/[0-9]{1,12}"))) return asset("mobile/boot.html");
        if (path.startsWith("/assets/")) return asset(path.substring(1));
        if (path.equals("/_streamvault/offline")) return asset("assets/offline.html");
        if (path.equals("/sw.js")) return asset("sw.js");
        if (path.startsWith("/_streamvault/mobile/") || path.startsWith("/_streamvault/pages/")) {
            return asset(path.substring("/_streamvault/".length()));
        }
        // Login, Access, APIs, streams and server-rendered administration always reach the server.
        return null;
    }

    static final class WorkerClient extends ServiceWorkerClient {
        private final MobileFrontend frontend;
        WorkerClient(MobileFrontend frontend) { this.frontend = frontend; }
        @Override public WebResourceResponse shouldInterceptRequest(WebResourceRequest request) {
            return frontend.intercept(request.getUrl(), request.getMethod(), false);
        }
    }

    private String read(String path) throws IOException {
        try (InputStream input = context.getAssets().open("web/" + path);
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] block = new byte[8192]; int count;
            while ((count = input.read(block)) != -1) output.write(block, 0, count);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private WebResourceResponse asset(String path) {
        if (!files.contains(path)) return null;
        String mime = path.endsWith(".js") ? "application/javascript" : path.endsWith(".css") ? "text/css"
                : path.endsWith(".woff2") ? "font/woff2" : URLConnection.guessContentTypeFromName(path);
        if (mime == null) mime = "application/octet-stream";
        try {
            InputStream body;
            if (path.endsWith(".html")) {
                ActivityManager manager = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
                String info = "<script>window.SV_ANDROID={version:" + JSONObject.quote(BuildConfig.VERSION_NAME)
                        + ",version_code:" + BuildConfig.VERSION_CODE + "};</script>";
                String html = read(path).replaceFirst("<head>", "<head>" + info);
                if (path.equals("assets/offline.html")) html = html.replace("</head>",
                        "<link rel=\"stylesheet\" href=\"/_streamvault/mobile/mobile.css\">"
                        + "<script defer src=\"/_streamvault/mobile/runtime.js\"></script></head>");
                if (manager != null && manager.isLowRamDevice()) html = html.replace("<html ", "<html data-native-effects=\"reduced\" ");
                body = new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8));
            } else body = context.getAssets().open("web/" + path);
            WebResourceResponse response = new WebResourceResponse(mime, mime.startsWith("text/") || mime.equals("application/javascript") ? "UTF-8" : null, body);
            Map<String, String> headers = new HashMap<>();
            headers.put("Cache-Control", "no-store"); headers.put("X-Content-Type-Options", "nosniff");
            headers.put("Content-Security-Policy", "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
                    + "form-action 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
                    + "img-src 'self' data: blob: https:; font-src 'self'; media-src 'self' blob: http: https:; "
                    + "connect-src 'self' blob: http: https: ws: wss:; frame-src http: https:; worker-src 'self' blob:");
            response.setResponseHeaders(headers);
            return response;
        } catch (IOException ignored) { return null; }
    }
}

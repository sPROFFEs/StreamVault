package com.streamvault.app;

import static org.junit.Assert.*;
import android.content.Context;
import android.net.Uri;
import android.webkit.WebResourceResponse;
import org.robolectric.RuntimeEnvironment;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;
import java.nio.charset.StandardCharsets;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 28)
public class MobileFrontendTest {
    @Test public void onlyExactServerOriginCanLoadInstalledAssetsAndBoot() throws Exception {
        Context context = RuntimeEnvironment.getApplication();
        MobileFrontend frontend = new MobileFrontend(context, "https://cinema.example:8443");
        WebResourceResponse page = frontend.intercept(Uri.parse("https://cinema.example:8443/media/7?type=tv"), "GET", true);
        assertNotNull(page);
        String html = new String(page.getData().readAllBytes(), StandardCharsets.UTF_8);
        assertTrue(html.contains("window.SV_ANDROID"));
        assertTrue(html.contains("mobile/boot.js"));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example/media/7"), "GET", true));
        assertNull(frontend.intercept(Uri.parse("https://evil.example/assets/js/about.js"), "GET", false));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/login"), "GET", true));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/cdn-cgi/access/login"), "GET", true));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/portal?hosted=1"), "GET", true));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/portal"), "POST", true));
        assertNotNull(frontend.intercept(Uri.parse("https://cinema.example:8443/assets/offline.html"), "GET", true));
        assertNotNull(frontend.intercept(Uri.parse("https://cinema.example:8443/sw.js"), "GET", false));
        assertNotNull(frontend.intercept(Uri.parse("https://cinema.example:8443/_streamvault/offline"), "GET", true));
        assertNotNull(frontend.intercept(Uri.parse("https://cinema.example:8443/portal"), "GET", false));
        WebResourceResponse about = frontend.intercept(Uri.parse("https://cinema.example:8443/about"), "GET", true);
        String installedAbout = new String(about.getData().readAllBytes(), StandardCharsets.UTF_8);
        assertTrue(installedAbout.contains("about-install"));
        assertFalse(installedAbout.contains("mobile/boot.js"));
        assertFalse(installedAbout.contains("SV_MOBILE_USERNAME"));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/assets/%2e%2e/secret"), "GET", false));
        assertNull(frontend.intercept(Uri.parse("https://cinema.example:8443/_streamvault/manifest.json"), "GET", false));
    }
}

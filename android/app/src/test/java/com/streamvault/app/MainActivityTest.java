package com.streamvault.app;

import static org.junit.Assert.*;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.webkit.ServiceWorkerClient;
import android.webkit.ServiceWorkerController;
import android.webkit.ServiceWorkerWebSettings;
import android.webkit.WebView;
import android.widget.EditText;
import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import java.net.URI;
import java.util.HashSet;
import java.util.Set;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.Implementation;
import org.robolectric.annotation.Implements;
import org.robolectric.shadows.ShadowWebView;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 28, shadows = {MainActivityTest.MessageFeature.class, MainActivityTest.MessageBridge.class,
        MainActivityTest.WorkerController.class})
public class MainActivityTest {
    @Before public void configureProvider() {
        RuntimeEnvironment.getApplication().getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                .edit().clear().commit();
        PackageInfo provider = new PackageInfo();
        provider.packageName = "com.android.webview";
        provider.versionName = "130.0.0.0";
        ShadowWebView.setCurrentWebViewPackage(provider);
        MessageBridge.origins = null;
        MessageBridge.listener = null;
    }

    @Test public void savingDomainStartsMainWithAPathlessBridgeOrigin() {
        try (ActivityController<SetupActivity> setup = Robolectric.buildActivity(SetupActivity.class,
                new Intent().putExtra(SetupActivity.EXTRA_FIRST_RUN, true)).create()) {
            ((EditText) setup.get().findViewById(R.id.url_input)).setText("Watch.Example.com:8443/");
            setup.get().findViewById(R.id.btn_save).performClick();
            Intent destination = Shadows.shadowOf(setup.get()).getNextStartedActivity();
            assertEquals(MainActivity.class.getName(), destination.getComponent().getClassName());
            assertEquals("https://watch.example.com:8443/", configuredServer());

            try (ActivityController<MainActivity> main = Robolectric.buildActivity(MainActivity.class, destination).create()) {
                assertEquals(Set.of("https://watch.example.com:8443"), MessageBridge.origins);
                WebView web = main.get().findViewById(R.id.webview);
                assertTrue(ServerUrl.sameOrigin(configuredServer(), web.getUrl()));

                // A provider may deliver a bridge callback before its first URL exists.
                MessageBridge.listener.onPostMessage(new WebView(main.get()),
                        new WebMessageCompat("{\"type\":\"check-update\"}"),
                        Uri.parse("https://watch.example.com:8443"), true, null);

                // Existing preferences/storage keep their format, while route joins avoid //.
                main.get().findViewById(R.id.offline_button).performClick();
                assertEquals("https://watch.example.com:8443/_streamvault/offline", web.getUrl());
                assertNotNull(new MobileFrontend(main.get(), configuredServer())
                        .intercept(Uri.parse(web.getUrl()), "GET", true));
                assertEquals("https://watch.example.com:8443/", configuredServer());
            }
        }
    }

    @Test public void reopeningAnExistingServerAlsoUsesAnExactBridgeOrigin() {
        RuntimeEnvironment.getApplication().getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                .edit().putString(MainActivity.KEY_ORIGIN, "https://watch.example.com/").commit();
        try (ActivityController<MainActivity> main = Robolectric.buildActivity(MainActivity.class).create()) {
            assertFalse(main.get().isFinishing());
            assertEquals(Set.of("https://watch.example.com"), MessageBridge.origins);
            assertEquals("https://watch.example.com/", configuredServer());
        }
    }

    private String configuredServer() {
        return RuntimeEnvironment.getApplication().getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE)
                .getString(MainActivity.KEY_ORIGIN, "");
    }

    @Implements(value = WebViewFeature.class, isInAndroidSdk = false)
    public static class MessageFeature {
        @Implementation protected static boolean isFeatureSupported(String feature) {
            return WebViewFeature.WEB_MESSAGE_LISTENER.equals(feature);
        }
    }

    @Implements(ServiceWorkerController.class)
    public static class WorkerController {
        @Implementation protected static ServiceWorkerController getInstance() {
            return new ServiceWorkerController() {
                @Override public ServiceWorkerWebSettings getServiceWorkerWebSettings() { return null; }
                @Override public void setServiceWorkerClient(ServiceWorkerClient client) { }
            };
        }
    }

    @Implements(value = WebViewCompat.class, isInAndroidSdk = false)
    public static class MessageBridge {
        static Set<String> origins;
        static WebViewCompat.WebMessageListener listener;

        @Implementation protected static void addWebMessageListener(WebView view, String name,
                Set<String> allowedOrigins, WebViewCompat.WebMessageListener callback) {
            // Model the provider's documented SCHEME://HOST[:PORT] contract.
            // Robolectric's default WebView does not implement this provider feature.
            for (String origin : allowedOrigins) {
                URI uri = URI.create(origin);
                if (!uri.getRawPath().isEmpty() || uri.getRawQuery() != null || uri.getRawFragment() != null
                        || uri.getHost() == null || uri.getRawUserInfo() != null || origin.contains("*")) {
                    throw new IllegalArgumentException("WebMessage origin rules must not contain a path");
                }
            }
            origins = new HashSet<>(allowedOrigins);
            listener = callback;
        }
    }
}

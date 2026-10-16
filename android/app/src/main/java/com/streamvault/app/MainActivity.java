package com.streamvault.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageInfo;
import android.graphics.Bitmap;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.CookieManager;
import android.webkit.JsResult;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

/** Hosted server-rendered UI; no native JavaScript bridge or packaged Python runtime. */
public final class MainActivity extends Activity {
    public static final String PREFS_NAME = "server";
    public static final String KEY_ORIGIN = "origin";

    private WebView webView;
    private ProgressBar progress;
    private View connectionErrorView;
    private TextView connectionErrorDetail;
    private ImageButton btnSettings;
    private SharedPreferences preferences;
    private String server = "";
    private String failedUrl;
    private boolean backPending;
    private String lastUrl;
    private boolean discardHistory;
    private int previousOrientation;
    private int previousSystemUi;
    private ConnectivityManager connectivity;
    private final ConnectivityManager.NetworkCallback networkCallback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) {
            handler.post(() -> { if (failedUrl != null && !server.isEmpty() && !isFinishing()) retry(); });
        }
    };
    private View fullscreen;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private final Handler handler = new Handler(Looper.getMainLooper());

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        PackageInfo provider = WebView.getCurrentWebViewPackage();
        if (provider == null || !WebViewVersion.supportsApp(provider.versionName)) {
            TextView unavailable = new TextView(this);
            unavailable.setText(provider == null ? R.string.webview_missing : R.string.webview_outdated);
            unavailable.setTextColor(android.graphics.Color.WHITE);
            unavailable.setTextSize(20);
            unavailable.setGravity(android.view.Gravity.CENTER);
            int padding = (int) (24 * getResources().getDisplayMetrics().density);
            unavailable.setPadding(padding, padding, padding, padding);
            setContentView(unavailable);
            return;
        }

        preferences = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String configured = preferences.getString(KEY_ORIGIN, BuildConfig.SERVER_URL);
        if (!configured.isEmpty()) {
            try { server = ServerUrl.normalize(configured, BuildConfig.DEBUG); }
            catch (IllegalArgumentException ignored) { server = ""; }
        }
        if (server.isEmpty()) {
            startActivity(new Intent(this, SetupActivity.class).putExtra(SetupActivity.EXTRA_FIRST_RUN, true));
            finish();
            return;
        }

        setContentView(R.layout.activity_main);
        webView = findViewById(R.id.webview);
        progress = findViewById(R.id.progress);
        connectionErrorView = findViewById(R.id.connection_error);
        connectionErrorDetail = findViewById(R.id.connection_error_detail);
        btnSettings = findViewById(R.id.btn_settings);

        findViewById(R.id.btn_retry).setOnClickListener(view -> retry());
        findViewById(R.id.offline_button).setOnClickListener(view -> {
            if (!server.isEmpty()) webView.loadUrl(server + "/assets/offline.html");
        });
        btnSettings.setOnClickListener(view -> {
            startActivity(new Intent(this, SetupActivity.class).putExtra(SetupActivity.EXTRA_FIRST_RUN, false));
        });

        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            findViewById(R.id.root).setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return WindowInsets.CONSUMED;
            });
        }
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::navigateBack);
        }

        configureWebView();
        connectivity = getSystemService(ConnectivityManager.class);
        if (connectivity != null) {
            connectivity.registerDefaultNetworkCallback(networkCallback);
        }
        if (state != null) lastUrl = state.getString("lastUrl");
        if (state == null || webView.restoreState(state) == null) {
            connect();
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setUserAgentString(settings.getUserAgentString() + " StreamVaultAndroid/" + BuildConfig.VERSION_NAME);

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String target = uri.toString();
                String scheme = uri.getScheme();
                boolean web = "https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme);
                if (!request.isForMainFrame()) return !ServerUrl.isAllowedSubframe(target);
                if (ServerUrl.sameOrigin(server, target) || ServerUrl.isCloudflareAuth(target)) {
                    return false;
                }
                if (web && request.hasGesture() && uri.getUserInfo() == null) {
                    openExternal(uri);
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (ServerUrl.sameOrigin(server, url)) lastUrl = url;
                failedUrl = null;
                connectionErrorView.setVisibility(View.GONE);
                progress.setVisibility(View.VISIBLE);
                updateSettingsButton(url);
            }

            @Override
            public void doUpdateVisitedHistory(WebView view, String url, boolean reload) {
                if (ServerUrl.sameOrigin(server, url)) lastUrl = url;
                updateSettingsButton(url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (discardHistory && ServerUrl.sameOrigin(server, url)) {
                    view.clearHistory();
                    discardHistory = false;
                }
                progress.setVisibility(View.GONE);
                if (failedUrl == null) {
                    connectionErrorView.setVisibility(View.GONE);
                }
                updateSettingsButton(url);
                CookieManager.getInstance().flush();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    showError(request.getUrl().toString(), error.getDescription().toString());
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                int status = response.getStatusCode();
                // 401 and 403 are authentication/challenge responses (e.g. Cloudflare Access).
                // Let the WebView render the challenge/login page instead of showing an error.
                if (request.isForMainFrame() && status != 401 && status != 403) {
                    showError(request.getUrl().toString(), "HTTP " + status);
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler sslHandler, SslError error) {
                sslHandler.cancel();
                showError(view.getUrl() != null ? view.getUrl() : server, getString(R.string.certificate_error));
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsConfirm(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(Uri.parse(url).getHost())
                        .setMessage(text)
                        .setPositiveButton(R.string.confirm, (dialog, which) -> result.confirm())
                        .setNegativeButton(R.string.cancel, (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle(Uri.parse(url).getHost())
                        .setMessage(text)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                if (value >= 100) progress.setVisibility(View.GONE);
            }

            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (fullscreen != null) { callback.onCustomViewHidden(); return; }
                previousOrientation = getRequestedOrientation();
                previousSystemUi = getWindow().getDecorView().getSystemUiVisibility();
                fullscreen = view;
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
                if (Build.VERSION.SDK_INT >= 30) {
                    getWindow().getInsetsController().hide(WindowInsets.Type.systemBars());
                    getWindow().getInsetsController().setSystemBarsBehavior(
                            WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                } else {
                    getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
                }
                fullscreenCallback = callback;
                ((FrameLayout) findViewById(R.id.root)).addView(view,
                        new FrameLayout.LayoutParams(-1, -1));
                findViewById(R.id.webview_container).setVisibility(View.GONE);
                btnSettings.setVisibility(View.GONE);
            }

            @Override
            public void onHideCustomView() {
                hideFullscreen();
            }
        });
    }

    private void updateSettingsButton(String url) {
        if (fullscreen != null) {
            btnSettings.setVisibility(View.GONE);
            return;
        }
        if (failedUrl != null) {
            btnSettings.setVisibility(View.VISIBLE);
            return;
        }
        if (url == null) {
            btnSettings.setVisibility(View.GONE);
            return;
        }
        Uri uri = Uri.parse(url);
        String path = uri.getPath() != null ? uri.getPath() : "";
        if (path.startsWith("/login") || path.startsWith("/setup") || ServerUrl.isCloudflareAuth(url)) {
            btnSettings.setVisibility(View.VISIBLE);
        } else {
            btnSettings.setVisibility(View.GONE);
        }
    }

    private void connect() {
        if (server.isEmpty()) {
            startActivity(new Intent(this, SetupActivity.class).putExtra(SetupActivity.EXTRA_FIRST_RUN, true));
            finish();
            return;
        }
        hideFullscreen();
        failedUrl = null;
        connectionErrorView.setVisibility(View.GONE);
        webView.loadUrl(server);
    }

    private void retry() {
        if (server.isEmpty()) {
            startActivity(new Intent(this, SetupActivity.class).putExtra(SetupActivity.EXTRA_FIRST_RUN, true));
            finish();
            return;
        }
        hideFullscreen();
        failedUrl = null;
        connectionErrorView.setVisibility(View.GONE);
        webView.loadUrl(ServerUrl.recoveryUrl(server, lastUrl));
    }

    private void openExternal(Uri uri) {
        String scheme = uri.getScheme();
        if (!("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme))
                || uri.getUserInfo() != null) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (ActivityNotFoundException ignored) {
            Toast.makeText(this, R.string.external_error, Toast.LENGTH_LONG).show();
        }
    }

    private void showError(String url, String detail) {
        if (!ServerUrl.sameOrigin(server, url) && !ServerUrl.isCloudflareAuth(url)) return;
        failedUrl = url;
        hideFullscreen();
        progress.setVisibility(View.GONE);
        connectionErrorDetail.setText(detail);
        connectionErrorView.setVisibility(View.VISIBLE);
        updateSettingsButton(url);
    }

    private void hideFullscreen() {
        if (fullscreen == null) return;
        ((FrameLayout) findViewById(R.id.root)).removeView(fullscreen);
        fullscreen = null;
        setRequestedOrientation(previousOrientation);
        if (Build.VERSION.SDK_INT >= 30) getWindow().getInsetsController().show(WindowInsets.Type.systemBars());
        else getWindow().getDecorView().setSystemUiVisibility(previousSystemUi);
        findViewById(R.id.webview_container).setVisibility(View.VISIBLE);
        updateSettingsButton(webView != null ? webView.getUrl() : null);
        if (fullscreenCallback != null) {
            fullscreenCallback.onCustomViewHidden();
            fullscreenCallback = null;
        }
    }

    private void navigateBack() {
        if (fullscreen != null) hideFullscreen();
        else if (webView == null) finish();
        else if (discardHistory) { if (failedUrl != null) finish(); }
        else if (!backPending) {
            backPending = true;
            webView.evaluateJavascript("Boolean(window.SVShell && window.SVShell.nativeBack && window.SVShell.nativeBack())", consumed -> {
                backPending = false;
                if (!"true".equals(consumed)) {
                    if (webView.canGoBack()) webView.goBack();
                    else finish();
                }
            });
        }
    }

    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() { navigateBack(); }

    @Override protected void onSaveInstanceState(Bundle state) {
        if (webView != null) webView.saveState(state);
        state.putString("lastUrl", lastUrl);
        super.onSaveInstanceState(state);
    }

    @Override protected void onPause() {
        if (webView != null) {
            webView.onPause();
            CookieManager.getInstance().flush();
        }
        super.onPause();
    }

    @Override protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
    }

    @Override protected void onDestroy() {
        if (connectivity != null) connectivity.unregisterNetworkCallback(networkCallback);
        handler.removeCallbacksAndMessages(null);
        hideFullscreen();
        if (webView != null) {
            ((FrameLayout) findViewById(R.id.webview_container)).removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }
}

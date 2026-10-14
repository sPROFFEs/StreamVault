package com.streamvault.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.webkit.JsResult;
import android.view.WindowInsetsController;
import android.graphics.Bitmap;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedDispatcher;

/** Hosted server-rendered UI; no native JavaScript bridge or packaged Python runtime. */
public final class MainActivity extends Activity {
    private WebView webView;
    private TextView status;
    private TextView message;
    private ProgressBar progress;
    private View feedback;
    private SharedPreferences preferences;
    private String server = "";
    private boolean failed;
    private boolean backPending;
    private String lastUrl;
    private boolean discardHistory;
    private int previousOrientation;
    private int previousSystemUi;
    private ConnectivityManager connectivity;
    private final ConnectivityManager.NetworkCallback networkCallback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network network) {
            handler.post(() -> { if (failed && !server.isEmpty() && !isFinishing()) retry(); });
        }
    };
    private View fullscreen;
    private WebChromeClient.CustomViewCallback fullscreenCallback;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable connectionTimeout = () -> {
        webView.stopLoading();
        showError(R.string.connection_error);
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        android.content.pm.PackageInfo provider = WebView.getCurrentWebViewPackage();
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
        setContentView(R.layout.activity_main);
        preferences = getSharedPreferences("server", MODE_PRIVATE);
        webView = findViewById(R.id.webview);
        status = findViewById(R.id.status);
        message = findViewById(R.id.message);
        progress = findViewById(R.id.progress);
        feedback = findViewById(R.id.feedback);
        findViewById(R.id.server_button).setOnClickListener(view -> chooseServer());
        findViewById(R.id.retry).setOnClickListener(view -> retry());
        findViewById(R.id.reload_button).setOnClickListener(view -> retry());
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
        connectivity.registerDefaultNetworkCallback(networkCallback);
        if (state != null) lastUrl = state.getString("lastUrl");
        String configured = preferences.getString("origin", BuildConfig.SERVER_URL);
        if (!configured.isEmpty()) {
            try { server = ServerUrl.normalize(configured, BuildConfig.DEBUG); }
            catch (IllegalArgumentException ignored) { server = ""; }
        }
        if (server.isEmpty()) {
            message.setText(R.string.setup_required);
            feedback.setVisibility(View.VISIBLE);
            findViewById(R.id.retry).setVisibility(View.GONE);
            chooseServer();
        } else if (state == null || webView.restoreState(state) == null) {
            connect();
        }
    }

    @SuppressLint("SetJavaScriptEnabled") // Required by the authenticated, explicitly selected server UI.
    private void configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setSupportMultipleWindows(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String target = request.getUrl().toString();
                String scheme = request.getUrl().getScheme();
                boolean web = "https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme);
                if (!request.isForMainFrame()) return !web;
                if (ServerUrl.sameOrigin(server, target)) return false;
                if (web && request.hasGesture() && request.getUrl().getUserInfo() == null) {
                    openExternal(request.getUrl());
                }
                return true;
            }
            @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (ServerUrl.sameOrigin(server, url)) lastUrl = url;
                failed = false;
                status.setText(R.string.loading);
                feedback.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
                progress.setVisibility(View.VISIBLE);
                handler.removeCallbacks(connectionTimeout);
                handler.postDelayed(connectionTimeout, 20000);
            }
            @Override public void doUpdateVisitedHistory(WebView view, String url, boolean reload) {
                if (ServerUrl.sameOrigin(server, url)) lastUrl = url;
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (discardHistory && ServerUrl.sameOrigin(server, url)) {
                    view.clearHistory();
                    discardHistory = false;
                }
                handler.removeCallbacks(connectionTimeout);
                progress.setVisibility(View.GONE);
                if (!failed) status.setText(R.string.connected);
                CookieManager.getInstance().flush();
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showError(R.string.connection_error);
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame()) showError(R.string.connection_error);
            }
            @Override public void onReceivedSslError(WebView view, SslErrorHandler sslHandler, SslError error) {
                sslHandler.cancel();
                showError(R.string.certificate_error);
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onJsConfirm(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setTitle(Uri.parse(url).getHost()).setMessage(text)
                        .setPositiveButton(R.string.confirm, (dialog, which) -> result.confirm())
                        .setNegativeButton(R.string.cancel, (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }
            @Override public boolean onJsAlert(WebView view, String url, String text, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setTitle(Uri.parse(url).getHost()).setMessage(text)
                        .setPositiveButton(android.R.string.ok, (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }
            @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean gesture, android.os.Message result) {
                if (!gesture) return false;
                // target=_blank has its own temporary client, with the same origin policy.
                WebView popup = new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient() {
                    @Override public boolean shouldOverrideUrlLoading(WebView window, WebResourceRequest request) {
                        String target = request.getUrl().toString();
                        if (ServerUrl.sameOrigin(server, target)) webView.loadUrl(target);
                        else openExternal(request.getUrl());
                        handler.post(popup::destroy);
                        return true;
                    }
                });
                ((WebView.WebViewTransport) result.obj).setWebView(popup);
                result.sendToTarget();
                return true;
            }
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); }
            @Override public void onShowCustomView(View view, CustomViewCallback callback) {
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
                ((FrameLayout) findViewById(R.id.content)).addView(view,
                        new FrameLayout.LayoutParams(-1, -1));
                webView.setVisibility(View.GONE);
                findViewById(R.id.toolbar).setVisibility(View.GONE);
            }
            @Override public void onHideCustomView() { hideFullscreen(); }
        });
    }

    private void chooseServer() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        content.setPadding(padding, 0, padding, 0);
        TextView help = new TextView(this);
        help.setText(BuildConfig.DEBUG ? R.string.server_help_debug : R.string.server_help);
        content.addView(help);
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint(R.string.server_hint);
        input.setText(server);
        content.addView(input);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle(R.string.server_title)
                .setView(content).setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.connect, null).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            final String selected;
            try { selected = ServerUrl.normalize(input.getText().toString(), BuildConfig.DEBUG); }
            catch (IllegalArgumentException exception) { input.setError(getString(R.string.invalid_server)); return; }
            dialog.dismiss();
            if (!selected.equals(server)) {
                discardHistory = true;
                webView.stopLoading();
                handler.removeCallbacks(connectionTimeout);
                CookieManager.getInstance().removeAllCookies(removed -> {
                    android.webkit.WebStorage.getInstance().deleteAllData();
                    webView.clearCache(true);
                    webView.clearHistory();
                    setServer(selected);
                });
            } else { setServer(selected); }
        }));
        dialog.show();
    }

    private void setServer(String selected) {
        server = selected;
        preferences.edit().putString("origin", server).apply();
        lastUrl = null;
        connect();
    }

    private void connect() {
        if (server.isEmpty()) { chooseServer(); return; }
        hideFullscreen();
        findViewById(R.id.retry).setVisibility(View.VISIBLE);
        webView.loadUrl(server);
    }

    private void retry() {
        if (server.isEmpty()) { chooseServer(); return; }
        hideFullscreen();
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

    private void showError(int text) {
        failed = true;
        hideFullscreen();
        handler.removeCallbacks(connectionTimeout);
        progress.setVisibility(View.GONE);
        status.setText(R.string.app_name);
        message.setText(text);
        feedback.setVisibility(View.VISIBLE);
        findViewById(R.id.retry).setVisibility(View.VISIBLE);
        webView.setVisibility(View.GONE);
    }

    private void hideFullscreen() {
        if (fullscreen == null) return;
        ((FrameLayout) findViewById(R.id.content)).removeView(fullscreen);
        fullscreen = null;
        setRequestedOrientation(previousOrientation);
        if (Build.VERSION.SDK_INT >= 30) getWindow().getInsetsController().show(WindowInsets.Type.systemBars());
        else getWindow().getDecorView().setSystemUiVisibility(previousSystemUi);
        findViewById(R.id.toolbar).setVisibility(View.VISIBLE);
        webView.setVisibility(failed ? View.GONE : View.VISIBLE);
        fullscreenCallback.onCustomViewHidden();
        fullscreenCallback = null;
    }

    private void navigateBack() {
        if (fullscreen != null) hideFullscreen();
        else if (webView == null) finish();
        else if (discardHistory) { if (failed) finish(); }
        else if (!backPending) {
            backPending = true;
            // No JavaScript interface is exposed to media provider frames.
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
            ((FrameLayout) findViewById(R.id.content)).removeView(webView);
            webView.destroy();
        }
        super.onDestroy();
    }
}

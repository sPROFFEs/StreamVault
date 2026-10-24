package com.streamvault.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Explicit verified APK downloads. No cookie transfer, arbitrary URLs, or silent installation. */
final class AppUpdater {
    interface Callback { void update(ClientRelease release, String message); }
    private final Activity activity;
    private final Callback callback;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private ClientRelease latest;
    private long checkedAt;
    private boolean checking, downloading;
    private volatile boolean closed;
    private File pendingInstall;

    AppUpdater(Activity activity, Callback callback) { this.activity = activity; this.callback = callback; }

    void check() {
        if (closed || checking) return;
        if (checkedAt > 0 && System.currentTimeMillis() - checkedAt < 300000) { publish(""); return; }
        checking = true;
        worker.execute(() -> {
            ClientRelease result = null;
            try {
                JSONObject release = json("https://api.github.com/repos/" + ClientRelease.REPOSITORY + "/releases/latest");
                String tag = release.optString("tag_name");
                if (!tag.matches("v[0-9]+\\.[0-9]+\\.[0-9]+")) throw new IOException("Invalid release tag");
                result = ClientRelease.parse(release, json(ClientRelease.HOME + "/releases/download/" + tag + "/release.json"));
            } catch (Exception ignored) { /* Keep authentication/network details out of diagnostics. */ }
            ClientRelease checked = result;
            main.post(() -> {
                checking = false; checkedAt = System.currentTimeMillis(); latest = checked;
                publish(checked == null ? activity.getString(R.string.update_check_failed) : "");
            });
        });
    }

    void publish(String message) { if (!closed) callback.update(latest, message); }

    void offerInstall() {
        if (closed || downloading) return;
        if (latest == null || !latest.isNewerThan(BuildConfig.VERSION_CODE)) { check(); return; }
        ClientRelease selected = latest;
        new AlertDialog.Builder(activity).setTitle(R.string.update_title)
            .setMessage(activity.getString(R.string.update_confirm, selected.version, (selected.size + 1048575) / 1048576))
            .setNegativeButton(R.string.cancel, null).setPositiveButton(R.string.update_download, (dialog, which) -> download(selected)).show();
    }

    private void download(ClientRelease selected) {
        downloading = true; publish(activity.getString(R.string.update_downloading));
        worker.execute(() -> {
            File directory = new File(activity.getCacheDir(), "apk-updates");
            File partial = new File(directory, "update.part"), apk = new File(directory, "update.apk");
            boolean complete = false;
            try {
                if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("No update directory");
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                HttpURLConnection connection = connection(selected.url);
                try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    byte[] block = new byte[65536]; long size = 0; int count;
                    while ((count = input.read(block)) != -1) {
                        if (closed || Thread.currentThread().isInterrupted()) throw new IOException("Cancelled");
                        size += count;
                        if (size > selected.size) throw new IOException("APK size mismatch");
                        digest.update(block, 0, count); output.write(block, 0, count);
                    }
                    if (size != selected.size || !hex(digest.digest()).equals(selected.sha256)) throw new IOException("APK integrity mismatch");
                } finally { connection.disconnect(); }
                verifyInstalledCompatibility(partial, selected);
                if (apk.exists() && !apk.delete()) throw new IOException("Previous update unavailable");
                if (!partial.renameTo(apk)) throw new IOException("Cannot prepare APK");
                complete = true;
                main.post(() -> { downloading = false; if (!closed) { pendingInstall = apk; install(); } });
            } catch (Exception ignored) {
                main.post(() -> { downloading = false; publish(activity.getString(R.string.update_download_failed)); });
            } finally { if (!complete && partial.exists() && !partial.delete()) partial.deleteOnExit(); }
        });
    }

    @SuppressWarnings("deprecation")
    private void verifyInstalledCompatibility(File apk, ClientRelease release) throws Exception {
        PackageManager manager = activity.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo incoming = manager.getPackageArchiveInfo(apk.getAbsolutePath(), flags);
        PackageInfo installed = manager.getPackageInfo(activity.getPackageName(), flags);
        if (incoming == null || !activity.getPackageName().equals(incoming.packageName)
                || !release.version.equals(incoming.versionName)
                || (Build.VERSION.SDK_INT >= 28 ? incoming.getLongVersionCode() : incoming.versionCode) != release.versionCode
                || !signatures(installed).equals(signatures(incoming)) || signatures(incoming).isEmpty()) throw new IOException("Incompatible APK");
    }

    @SuppressWarnings("deprecation")
    private static Set<String> signatures(PackageInfo info) throws Exception {
        Signature[] signatures = Build.VERSION.SDK_INT >= 28 && info.signingInfo != null
                ? info.signingInfo.getApkContentsSigners() : info.signatures;
        Set<String> result = new HashSet<>();
        if (signatures != null) for (Signature signature : signatures) result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        return result;
    }

    void install() {
        if (closed || pendingInstall == null || !pendingInstall.isFile()) return;
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(activity).setTitle(R.string.update_title).setMessage(R.string.update_permission)
                .setNegativeButton(R.string.cancel, (dialog, which) -> pendingInstall = null)
                .setPositiveButton(R.string.confirm, (dialog, which) -> {
                    try { activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.getPackageName()))); }
                    catch (android.content.ActivityNotFoundException ignored) { publish(activity.getString(R.string.update_download_failed)); }
                }).show();
            return;
        }
        Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".updates", pendingInstall);
        pendingInstall = null;
        try {
            activity.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION));
            publish(activity.getString(R.string.update_ready));
        } catch (android.content.ActivityNotFoundException ignored) { publish(activity.getString(R.string.update_download_failed)); }
    }

    void resume() { if (pendingInstall != null && activity.getPackageManager().canRequestPackageInstalls()) install(); else check(); }
    void close() { closed = true; worker.shutdownNow(); }

    static boolean trustedUrl(String value) {
        try {
            URL url = new URL(value);
            return url.getProtocol().equals("https") && url.getUserInfo() == null && (url.getPort() == -1 || url.getPort() == 443)
                && Arrays.asList("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com").contains(url.getHost());
        } catch (Exception ignored) { return false; }
    }

    private static HttpURLConnection connection(String value) throws IOException {
        for (int redirect = 0; redirect < 6; redirect++) {
            if (!trustedUrl(value)) throw new IOException("Untrusted release host");
            HttpURLConnection connection = (HttpURLConnection) new URL(value).openConnection();
            connection.setConnectTimeout(10000); connection.setReadTimeout(10000); connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("User-Agent", "StreamVault-Android-Updater");
            int status = connection.getResponseCode();
            if (status >= 300 && status < 400) {
                String target = connection.getHeaderField("Location"); connection.disconnect();
                if (target == null) throw new IOException("Invalid release redirect");
                value = new URL(new URL(value), target).toString(); continue;
            }
            if (status != 200) { connection.disconnect(); throw new IOException("Release unavailable"); }
            return connection;
        }
        throw new IOException("Too many release redirects");
    }

    private static JSONObject json(String url) throws Exception {
        HttpURLConnection connection = connection(url);
        try (InputStream input = connection.getInputStream(); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] block = new byte[8192]; int count;
            while ((count = input.read(block)) != -1) {
                if (output.size() + count > 512 * 1024) throw new IOException("Release metadata too large");
                output.write(block, 0, count);
            }
            return new JSONObject(output.toString(StandardCharsets.UTF_8.name()));
        } finally { connection.disconnect(); }
    }

    private static String hex(byte[] data) {
        StringBuilder value = new StringBuilder();
        for (byte part : data) value.append(String.format(java.util.Locale.ROOT, "%02x", part & 255));
        return value.toString();
    }
}

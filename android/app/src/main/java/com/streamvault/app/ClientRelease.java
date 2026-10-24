package com.streamvault.app;

import org.json.JSONArray;
import org.json.JSONObject;

/** Validated metadata from this project's official stable release, never supplied by web messages. */
final class ClientRelease {
    static final String REPOSITORY = "sPROFFEs/StreamVault";
    static final String HOME = "https://github.com/" + REPOSITORY;
    final String version, url, sha256, notes;
    final int versionCode;
    final long size;

    private ClientRelease(String version, int code, String url, String sha256, long size, String notes) {
        this.version = version; this.versionCode = code; this.url = url;
        this.sha256 = sha256; this.size = size; this.notes = notes;
    }

    boolean isNewerThan(int code) { return versionCode > code; }

    static ClientRelease parse(JSONObject release, JSONObject manifest) {
        String tag = release.optString("tag_name");
        Object code = manifest.opt("android_version_code");
        if (!tag.matches("v[0-9]+\\.[0-9]+\\.[0-9]+") || release.optBoolean("draft") || release.optBoolean("prerelease")
                || manifest.optInt("schema_version") != 1 || !REPOSITORY.equals(manifest.optString("repository"))
                || !tag.equals(manifest.optString("tag")) || !tag.substring(1).equals(manifest.optString("version"))
                || !(code instanceof Integer) || (int) code < 1 || (int) code > 2100000000) throw new IllegalArgumentException("Invalid official release");
        String version = tag.substring(1), name = "streamvault-" + version + "-android.apk";
        String url = HOME + "/releases/download/" + tag + "/" + name;
        JSONArray assets = release.optJSONArray("assets"), verified = manifest.optJSONArray("assets");
        if (assets == null || verified == null) throw new IllegalArgumentException("No APK assets");
        for (int index = 0; index < assets.length(); index++) {
            JSONObject asset = assets.optJSONObject(index);
            if (asset == null || !name.equals(asset.optString("name")) || !url.equals(asset.optString("browser_download_url"))) continue;
            for (int other = 0; other < verified.length(); other++) {
                JSONObject item = verified.optJSONObject(other);
                if (item == null) continue;
                long size = item.optLong("size");
                String hash = item.optString("sha256");
                if (name.equals(item.optString("name")) && size > 0 && size <= 500L * 1024 * 1024
                        && asset.optLong("size") == size && hash.matches("[a-f0-9]{64}")) {
                    String notes = release.optString("body", "");
                    return new ClientRelease(version, (int) code, url, hash, size, notes.substring(0, Math.min(12000, notes.length())));
                }
            }
        }
        throw new IllegalArgumentException("No verified official APK");
    }

    JSONObject json() {
        try {
            return new JSONObject().put("version", version).put("url", HOME + "/releases/tag/v" + version).put("notes", notes)
                .put("android", new JSONObject().put("version", version).put("version_code", versionCode).put("url", url)
                .put("sha256", sha256).put("size", size));
        } catch (org.json.JSONException impossible) { throw new IllegalStateException(impossible); }
    }
}

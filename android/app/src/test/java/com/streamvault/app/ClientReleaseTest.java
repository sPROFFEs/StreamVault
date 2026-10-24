package com.streamvault.app;

import static org.junit.Assert.*;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.annotation.Config;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 28)
public class ClientReleaseTest {
    @Test public void validatesOfficialApkNameHashSizeAndMonotonicCode() throws Exception {
        JSONObject release = new JSONObject("{tag_name:'v1.0.3',assets:[{name:'streamvault-1.0.3-android.apk',size:1234,browser_download_url:'https://github.com/sPROFFEs/StreamVault/releases/download/v1.0.3/streamvault-1.0.3-android.apk'}]}");
        JSONObject manifest = new JSONObject("{schema_version:1,repository:'sPROFFEs/StreamVault',tag:'v1.0.3',version:'1.0.3',android_version_code:6,assets:[{name:'streamvault-1.0.3-android.apk',size:1234,sha256:'" + "a".repeat(64) + "'}]}");
        ClientRelease parsed = ClientRelease.parse(release, manifest);
        assertEquals(6, parsed.versionCode);
        assertTrue(parsed.isNewerThan(5));
        assertFalse(parsed.isNewerThan(6));
        assertEquals(1234, parsed.size);
        release.getJSONArray("assets").getJSONObject(0).put("browser_download_url", "https://evil.example/app.apk");
        try { ClientRelease.parse(release, manifest); fail("Untrusted APK accepted"); }
        catch (IllegalArgumentException expected) { }
    }

    @Test public void blocksNonHttpsAndNonGithubRedirects() {
        assertTrue(AppUpdater.trustedUrl("https://api.github.com/repos/sPROFFEs/StreamVault/releases/latest"));
        assertTrue(AppUpdater.trustedUrl("https://release-assets.githubusercontent.com/file?signature=public"));
        assertFalse(AppUpdater.trustedUrl("http://github.com/file"));
        assertFalse(AppUpdater.trustedUrl("https://github.com.evil.example/file"));
        assertFalse(AppUpdater.trustedUrl("https://user:password@github.com/file"));
        assertFalse(AppUpdater.trustedUrl("https://github.com:8443/file"));
    }
}

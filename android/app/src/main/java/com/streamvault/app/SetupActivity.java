package com.streamvault.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebStorage;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

public final class SetupActivity extends Activity {
    public static final String EXTRA_FIRST_RUN = "first_run";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);

        boolean firstRun = getIntent().getBooleanExtra(EXTRA_FIRST_RUN, false);
        SharedPreferences preferences = getSharedPreferences(MainActivity.PREFS_NAME, Context.MODE_PRIVATE);

        TextView titleView = findViewById(R.id.setup_title);
        TextView subtitleView = findViewById(R.id.setup_subtitle);
        EditText urlInput = findViewById(R.id.url_input);
        Button saveBtn = findViewById(R.id.btn_save);
        Button cancelBtn = findViewById(R.id.btn_cancel);

        if (firstRun) {
            titleView.setText(R.string.setup_welcome_title);
            subtitleView.setText(R.string.setup_welcome_subtitle);
            cancelBtn.setVisibility(View.GONE);
        } else {
            titleView.setText(R.string.setup_change_title);
            subtitleView.setText(R.string.setup_change_subtitle);
            String current = preferences.getString(MainActivity.KEY_ORIGIN, "");
            urlInput.setText(current);
            cancelBtn.setVisibility(View.VISIBLE);
        }

        cancelBtn.setOnClickListener(v -> finish());

        saveBtn.setOnClickListener(v -> {
            String url = urlInput.getText().toString().trim();
            if (url.isEmpty()) {
                urlInput.setError(getString(R.string.setup_error_empty_url));
                return;
            }

            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                url = "https://" + url;
            }
            while (url.endsWith("/")) {
                url = url.substring(0, url.length() - 1);
            }

            String normalized;
            try {
                normalized = ServerUrl.normalize(url, BuildConfig.DEBUG);
            } catch (IllegalArgumentException e) {
                urlInput.setError(getString(R.string.setup_error_invalid_url));
                return;
            }

            String previous = preferences.getString(MainActivity.KEY_ORIGIN, "");
            if (!normalized.equals(previous)) {
                CookieManager.getInstance().removeAllCookies(null);
                WebStorage.getInstance().deleteAllData();
            }

            preferences.edit().putString(MainActivity.KEY_ORIGIN, normalized).apply();
            Toast.makeText(this, R.string.setup_toast_saved, Toast.LENGTH_SHORT).show();

            Intent intent = new Intent(this, MainActivity.class);
            intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
            startActivity(intent);
            finish();
        });
    }
}

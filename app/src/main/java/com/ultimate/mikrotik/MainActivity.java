package com.ultimate.mikrotik;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.content.Context;
import android.content.SharedPreferences;
import androidx.security.crypto.EncryptedSharedPreferences;
import androidx.security.crypto.MasterKey;
import android.app.DownloadManager;
import android.net.Uri;
import android.os.Environment;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class MainActivity extends Activity {
    private static final String PREF = "router_config";
    private WebView webView;
    private LinearLayout root;
    private ProgressBar progress;
    private volatile boolean serverStarted = false;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(8, 13, 24));
        getWindow().setNavigationBarColor(Color.rgb(8, 13, 24));
        SharedPreferences p = prefs();
        if (p.getBoolean("configured", false)) startDashboard(); else showSetup();
    }

    private SharedPreferences prefs() {
        try {
            MasterKey key = new MasterKey.Builder(this)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build();
            return EncryptedSharedPreferences.create(this, PREF, key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM);
        } catch (Exception e) {
            throw new IllegalStateException("Cannot initialize encrypted credential storage", e);
        }
    }
    private int dp(float v) { return (int)(v * getResources().getDisplayMetrics().density); }
    private TextView label(String s) {
        TextView t = new TextView(this); t.setText(s); t.setTextColor(Color.rgb(220,232,248));
        t.setTextSize(14); t.setPadding(0, dp(10), 0, dp(4)); return t;
    }
    private EditText field(String hint, String value, boolean password) {
        EditText e = new EditText(this); e.setSingleLine(true); e.setTextColor(Color.WHITE);
        e.setHintTextColor(Color.GRAY); e.setHint(hint); e.setText(value);
        e.setTextSize(15); e.setPadding(dp(12), dp(9), dp(12), dp(9));
        e.setBackgroundColor(Color.rgb(20,31,49));
        if (password) e.setInputType(129);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, dp(48)); lp.bottomMargin = dp(5); e.setLayoutParams(lp);
        return e;
    }
    private void showSetup() {
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(22), dp(28), dp(22), dp(20)); root.setBackgroundColor(Color.rgb(8,13,24));
        TextView title = new TextView(this); title.setText("◈ MikroTik Ultimate Manager"); title.setTextColor(Color.rgb(91,215,232)); title.setTextSize(22); title.setGravity(Gravity.CENTER); title.setPadding(0,dp(8),0,dp(8)); root.addView(title);
        TextView sub = new TextView(this); sub.setText("Connect your RouterOS device"); sub.setTextColor(Color.LTGRAY); sub.setGravity(Gravity.CENTER); sub.setPadding(0,0,0,dp(16)); root.addView(sub);
        SharedPreferences p = prefs();
        EditText host = field("Router IP", p.getString("host", "10.10.10.254"), false);
        EditText port = field("API Port", p.getString("port", "8728"), false);
        EditText user = field("Router username", p.getString("user", "admin"), false);
        EditText pass = field("Router password", "", true);
        EditText appPass = field("Create app login password", "", true);
        root.addView(label("Router IP address")); root.addView(host);
        root.addView(label("RouterOS API port")); root.addView(port);
        root.addView(label("Router username")); root.addView(user);
        root.addView(label("Router password")); root.addView(pass);
        root.addView(label("App login password")); root.addView(appPass);
        CheckBox tls = new CheckBox(this); tls.setText("Use API-SSL (8729 by default)"); tls.setTextColor(Color.LTGRAY); root.addView(tls);
        TextView note = new TextView(this); note.setText("Use a dedicated RouterOS API account with only the permissions this dashboard needs. Do not expose API ports to the public internet."); note.setTextColor(Color.GRAY); note.setTextSize(12); note.setPadding(0,dp(12),0,dp(12)); root.addView(note);
        Button save = new Button(this); save.setText("SAVE & CONNECT"); root.addView(save);
        save.setOnClickListener(v -> {
            String h=host.getText().toString().trim(), po=port.getText().toString().trim(), u=user.getText().toString().trim(), pw=pass.getText().toString(), ap=appPass.getText().toString();
            if(h.isEmpty()||po.isEmpty()||u.isEmpty()||pw.isEmpty()||ap.length()<8){ Toast.makeText(this,"Fill all fields. App password must be at least 8 characters.",Toast.LENGTH_LONG).show(); return; }
            try { Integer.parseInt(po); } catch(Exception ex){ Toast.makeText(this,"Invalid API port",Toast.LENGTH_SHORT).show(); return; }
            p.edit().putString("host",h).putString("port",po).putString("user",u).putString("router_pass",pw).putString("app_pass",ap).putBoolean("tls",tls.isChecked()).putBoolean("configured",true).apply();
            startDashboard();
        });
        setContentView(root);
    }

    private void startDashboard() {
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(Color.rgb(8,13,24));
        progress = new ProgressBar(this); root.addView(progress, new LinearLayout.LayoutParams(-1, dp(4)));
        webView = new WebView(this); webView.setBackgroundColor(Color.rgb(8,13,24));
        webView.getSettings().setJavaScriptEnabled(true); webView.getSettings().setDomStorageEnabled(true);
        webView.setWebChromeClient(new WebChromeClient()); webView.setWebViewClient(new WebViewClient(){
            @Override public void onPageFinished(WebView view, String url){ progress.setVisibility(View.GONE); }
            @Override public void onReceivedError(WebView view, android.webkit.WebResourceRequest req, android.webkit.WebResourceError err){ if(req.isForMainFrame()) Toast.makeText(MainActivity.this,"Dashboard connection error",Toast.LENGTH_SHORT).show(); }
        });
        CookieManager.getInstance().setAcceptCookie(true);
        webView.setDownloadListener((url, ua, disposition, mime, length) -> {
            DownloadManager.Request r = new DownloadManager.Request(Uri.parse(url));
            r.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            r.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "mikrotik-vouchers.csv");
            ((DownloadManager)getSystemService(DOWNLOAD_SERVICE)).enqueue(r);
            Toast.makeText(this,"Downloading voucher CSV…",Toast.LENGTH_SHORT).show();
        });
        root.addView(webView, new LinearLayout.LayoutParams(-1,0,1)); setContentView(root);
        if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
        SharedPreferences p = prefs();
        new Thread(() -> {
            try {
                Python.getInstance().getModule("android_bridge").callAttr("start_server",
                    p.getString("host","10.10.10.254"), Integer.parseInt(p.getString("port","8728")),
                    p.getString("user","admin"), p.getString("router_pass",""),
                    p.getString("app_pass",""), p.getBoolean("tls",false));
            } catch(Exception e) { runOnUiThread(() -> Toast.makeText(this,"Server error: "+e.getMessage(),Toast.LENGTH_LONG).show()); }
        }).start();
        webView.postDelayed(() -> webView.loadUrl("http://127.0.0.1:8765/"), 1400);
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack(); else super.onBackPressed();
    }
}

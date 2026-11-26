package com.pbrpro;

import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.content.Intent;
import android.net.Uri;
import android.widget.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class MainActivity extends Activity {
    EditText editor;
    TextView terminal, status;
    LinearLayout root;
    Thread runThread;
    String lastOutput = "Terminal output will appear here.";
    static final int PICK_PY = 77;

    int bg = Color.rgb(10, 13, 18), panel = Color.rgb(21, 27, 36);
    int green = Color.rgb(75, 220, 145), white = Color.WHITE;

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
        buildUI();
    }

    TextView text(String s, int size, int color) {
        TextView t = new TextView(this); t.setText(s); t.setTextSize(size);
        t.setTextColor(color); t.setPadding(12, 10, 12, 10); return t;
    }
    Button btn(String s) {
        Button b = new Button(this); b.setText(s); b.setTextColor(white);
        b.setAllCaps(false); b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(panel));
        return b;
    }
    void buildUI() {
        root = new LinearLayout(this); root.setOrientation(1); root.setPadding(14,14,14,14); root.setBackgroundColor(bg);
        setContentView(root);
        root.addView(text("PYTHON BUILDER  +  RUNNER PRO", 20, green));
        root.addView(text("Edit • Import • Run • Libraries • Build", 12, Color.LTGRAY));
        LinearLayout nav = new LinearLayout(this);
        for (String tab : new String[]{"EDITOR","TERMINAL","LIBRARIES","BUILD"}) {
            Button b = btn(tab); nav.addView(b, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            b.setOnClickListener(v -> showTab(tab));
        }
        root.addView(nav);
        status = text("Ready • Python 3.11", 12, green); root.addView(status);
        showTab("EDITOR");
    }
    void clearContent() {
        while (root.getChildCount() > 3) root.removeViewAt(3);
    }
    void showTab(String tab) {
        clearContent();
        if (tab.equals("EDITOR")) {
            LinearLayout actions = new LinearLayout(this);
            Button imp=btn("Import .py"), run=btn("▶ Run"), stop=btn("■ Stop");
            actions.addView(imp,new LinearLayout.LayoutParams(0,-2,1));
            actions.addView(run,new LinearLayout.LayoutParams(0,-2,1));
            actions.addView(stop,new LinearLayout.LayoutParams(0,-2,1));
            root.addView(actions);
            editor = new EditText(this); editor.setGravity(Gravity.TOP|Gravity.LEFT);
            editor.setTextColor(white); editor.setHintTextColor(Color.GRAY); editor.setHint("# Write Python code here\\nprint('Hello from Android!')");
            editor.setText("print('Hello from Python Builder Pro')\\n");
            editor.setTextSize(14); editor.setTypeface(android.graphics.Typeface.MONOSPACE);
            editor.setBackgroundColor(panel); editor.setPadding(12,12,12,12);
            root.addView(editor,new LinearLayout.LayoutParams(-1,0,1));
            imp.setOnClickListener(v -> importFile());
            run.setOnClickListener(v -> runCode());
            stop.setOnClickListener(v -> { if(runThread!=null) { runThread.interrupt(); status.setText("Stop requested"); }});
        } else if (tab.equals("TERMINAL")) {
            terminal = text(lastOutput, 13, green);
            terminal.setTypeface(android.graphics.Typeface.MONOSPACE); terminal.setBackgroundColor(Color.BLACK);
            ScrollView sc = new ScrollView(this); sc.addView(terminal); root.addView(sc,new LinearLayout.LayoutParams(-1,0,1));
            Button clear=btn("Clear terminal"); root.addView(clear); clear.setOnClickListener(v->{ lastOutput=""; terminal.setText(""); });
        } else if (tab.equals("LIBRARIES")) {
            root.addView(text("Included Python packages",18,green));
            root.addView(text("✓ requests\\n✓ colorama\\n✓ six\\n\\nThese packages are bundled into the APK at build time. To add more, add a supported package in app/build.gradle under chaquopy.defaultConfig.pip, then rebuild.",14,white));
            root.addView(text("Note: Not every PyPI package supports Android/Chaquopy. Packages with native binaries may not install.",12,Color.LTGRAY));
        } else if (tab.equals("BUILD")) {
            root.addView(text("Build your Android APK",20,green));
            root.addView(text("This app runs Python scripts on-device. To create a new APK containing your own script, add the script as app/src/main/python/user_script.py in this project and rebuild with GitHub Actions.",14,white));
            Button open=btn("Open GitHub Actions"); root.addView(open);
            open.setOnClickListener(v->{ Intent i=new Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/PMAX6278/H/actions")); startActivity(i); });
            root.addView(text("Build workflow: push project changes → GitHub Actions → latest successful run → Artifacts → download APK ZIP.",13,Color.LTGRAY));
        }
    }
    void importFile() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.setType("text/x-python"); i.addCategory(Intent.CATEGORY_OPENABLE);
        try { startActivityForResult(i,PICK_PY); }
        catch(Exception e) { i.setType("*/*"); startActivityForResult(i,PICK_PY); }
    }
    @Override protected void onActivityResult(int req,int res,Intent data) {
        super.onActivityResult(req,res,data);
        if(req==PICK_PY && res==RESULT_OK && data!=null) {
            try(InputStream in=getContentResolver().openInputStream(data.getData())) {
                ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] buf=new byte[4096]; int n;
                while((n=in.read(buf))>0) out.write(buf,0,n);
                editor.setText(out.toString("UTF-8")); status.setText("Imported: "+data.getData().getLastPathSegment());
            } catch(Exception e) { status.setText("Import error: "+e.getMessage()); }
        }
    }
    void runCode() {
        final String code=editor.getText().toString();
        status.setText("Running...");
        runThread = new Thread(() -> {
            try {
                String output=Python.getInstance().getModule("runner").callAttr("run_code",code).toString();
                runOnUiThread(()->{ status.setText("Finished"); showTerminalOutput(output); });
            } catch(Exception e) {
                runOnUiThread(()->{ status.setText("Python error"); showTerminalOutput(e.toString()); });
            }
        });
        runThread.start();
    }
    void showTerminalOutput(String s) {
        lastOutput = s;
        Toast.makeText(this, "Python run finished. Open TERMINAL tab.", Toast.LENGTH_SHORT).show();
    }
}

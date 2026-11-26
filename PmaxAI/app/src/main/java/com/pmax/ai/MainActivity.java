package com.pmax.ai;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.RecognitionListener;
import android.speech.tts.TextToSpeech;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class MainActivity extends Activity {
    private static final int REQ_MIC = 201;
    private final int bg=Color.rgb(9,12,19), panel=Color.rgb(22,29,40), green=Color.rgb(80,225,158);
    LinearLayout root, messages;
    EditText input, apiKey, memoryBox;
    ScrollView chatScroll;
    TextView status;
    Button sendBtn, voiceBtn, speakBtn;
    String latestAnswer = "";
    SharedPreferences prefs;
    TextToSpeech tts;
    SpeechRecognizer recognizer;
    boolean busy=false;
    JSONArray history = new JSONArray();

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        prefs=getSharedPreferences("pmax_ai",MODE_PRIVATE);
        loadHistory();
        tts=new TextToSpeech(this, code -> {});
        buildUi();
        renderHistory();
    }

    TextView txt(String s,int size,int color) {
        TextView t=new TextView(this); t.setText(s); t.setTextSize(size); t.setTextColor(color);
        t.setPadding(8,7,8,7); return t;
    }
    Button button(String s) {
        Button b=new Button(this); b.setText(s); b.setAllCaps(false); b.setTextColor(Color.WHITE);
        b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(panel)); return b;
    }
    EditText edit(String hint,int lines) {
        EditText e=new EditText(this); e.setHint(hint); e.setTextColor(Color.WHITE); e.setHintTextColor(Color.GRAY);
        e.setTextSize(14); e.setMinLines(lines); e.setPadding(12,8,12,8); e.setBackgroundColor(panel);
        e.setGravity(Gravity.TOP|Gravity.LEFT); return e;
    }
    void buildUi() {
        root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(12,10,12,8); root.setBackgroundColor(bg);
        setContentView(root);
        LinearLayout header=new LinearLayout(this); header.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout brand=new LinearLayout(this); brand.setOrientation(1);
        brand.addView(txt("PMAX AI",22,green)); brand.addView(txt("SMART PERSONAL ASSISTANT • MY / EN",10,Color.LTGRAY));
        header.addView(brand,new LinearLayout.LayoutParams(0,-2,1));
        Button settings=button("⚙"); header.addView(settings); root.addView(header);
        status=txt("Ready • Add your Gemini API key in Settings",11,green); root.addView(status);
        chatScroll=new ScrollView(this); messages=new LinearLayout(this); messages.setOrientation(1); messages.setPadding(2,8,2,8);
        chatScroll.addView(messages); root.addView(chatScroll,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout composer=new LinearLayout(this); composer.setGravity(Gravity.BOTTOM);
        input=edit("Message Pmax…",2); composer.addView(input,new LinearLayout.LayoutParams(0,-2,1));
        voiceBtn=button("🎙"); composer.addView(voiceBtn);
        sendBtn=button("➤"); composer.addView(sendBtn); root.addView(composer);
        speakBtn=button("🔊 Speak latest"); root.addView(speakBtn);
        sendBtn.setOnClickListener(v->sendMessage());
        voiceBtn.setOnClickListener(v->startVoice());
        speakBtn.setOnClickListener(v->speakLatest());
        settings.setOnClickListener(v->showSettings());
    }
    void showSettings() {
        LinearLayout box=new LinearLayout(this); box.setOrientation(1); box.setPadding(20,10,20,10);
        TextView title=txt("Pmax AI Settings",19,green); box.addView(title);
        apiKey=edit("Gemini API key (stored on this device)",1);
        apiKey.setText(prefs.getString("api_key","")); box.addView(apiKey);
        box.addView(txt("Personal memory (saved locally on this phone)",13,Color.LTGRAY));
        memoryBox=edit("Facts or preferences Pmax should remember",4);
        memoryBox.setText(prefs.getString("memory","")); box.addView(memoryBox);
        new android.app.AlertDialog.Builder(this).setView(box)
            .setPositiveButton("Save",(d,w)->{
                prefs.edit().putString("api_key",apiKey.getText().toString().trim())
                    .putString("memory",memoryBox.getText().toString().trim()).apply();
                status.setText("Settings saved on this device.");
            }).setNegativeButton("Cancel",null).show();
    }
    void addBubble(String who,String body,boolean user) {
        LinearLayout card=new LinearLayout(this); card.setOrientation(1); card.setPadding(10,8,10,8);
        card.setBackgroundColor(user?Color.rgb(26,65,54):panel);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);
        lp.setMargins(user?42:0,5,user?0:42,5); card.setLayoutParams(lp);
        card.addView(txt(who,10,user?green:Color.LTGRAY));
        TextView content=txt(body,14,Color.WHITE); content.setTextIsSelectable(true); card.addView(content);
        messages.addView(card);
        chatScroll.post(()->chatScroll.fullScroll(View.FOCUS_DOWN));
    }
    void renderHistory() {
        messages.removeAllViews();
        if(history.length()==0) addBubble("PMAX","မင်္ဂလာပါ။ ကျွန်တော် Pmax ပါ။ ဘာကူညီပေးရမလဲ?\nHello! I'm Pmax. How can I help?",false);
        for(int i=0;i<history.length();i++) {
            JSONObject m=history.optJSONObject(i);
            addBubble(m.optString("role").equals("user")?"YOU":"PMAX",m.optString("text"),m.optString("role").equals("user"));
        }
    }
    void loadHistory() {
        try { history=new JSONArray(prefs.getString("history","[]")); }
        catch(Exception e) { history=new JSONArray(); }
    }
    void saveHistory() { prefs.edit().putString("history",history.toString()).apply(); }
    void appendHistory(String role,String text) {
        try { history.put(new JSONObject().put("role",role).put("text",text)); saveHistory(); }
        catch(Exception ignored) {}
    }
    void sendMessage() {
        String q=input.getText().toString().trim();
        if(q.isEmpty()||busy) return;
        String key=prefs.getString("api_key","");
        if(key.isEmpty()) { status.setText("Open ⚙ Settings and add a Gemini API key first."); return; }
        input.setText(""); addBubble("YOU",q,true); appendHistory("user",q);
        busy=true; sendBtn.setEnabled(false); status.setText("Pmax is thinking…");
        new Thread(()->callGemini(q,key)).start();
    }
    void callGemini(String q,String key) {
        HttpURLConnection c=null;
        try {
            JSONArray contents=new JSONArray();
            String memory=prefs.getString("memory","");
            JSONObject system=new JSONObject().put("parts",new JSONArray().put(new JSONObject().put("text",
                "You are Pmax, a smart, helpful bilingual personal AI assistant. Reply in the language used by the user; support Burmese and English. Be accurate, clear, and honest about uncertainty. Do not claim to have performed actions you cannot perform. Use concise useful answers. The user's locally saved memory, which may be empty: "+memory))));
            for(int i=Math.max(0,history.length()-16);i<history.length();i++) {
                JSONObject m=history.getJSONObject(i);
                contents.put(new JSONObject().put("role",m.optString("role").equals("user")?"user":"model")
                    .put("parts",new JSONArray().put(new JSONObject().put("text",m.optString("text")))));
            }
            JSONObject body=new JSONObject().put("system_instruction",system)
                .put("contents",contents)
                .put("tools",new JSONArray().put(new JSONObject().put("google_search",new JSONObject())));
            URL u=new URL("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key="+URLEncoder.encode(key,"UTF-8"));
            c=(HttpURLConnection)u.openConnection(); c.setRequestMethod("POST"); c.setConnectTimeout(20000); c.setReadTimeout(90000);
            c.setDoOutput(true); c.setRequestProperty("Content-Type","application/json; charset=UTF-8");
            try(OutputStream o=c.getOutputStream()){o.write(body.toString().getBytes(StandardCharsets.UTF_8));}
            int code=c.getResponseCode(); InputStream is=code>=200&&code<300?c.getInputStream():c.getErrorStream();
            String raw=read(is);
            if(code<200||code>=300) throw new Exception("Gemini API "+code+": "+raw);
            JSONObject result=new JSONObject(raw);
            StringBuilder answer=new StringBuilder();
            JSONArray parts=result.getJSONArray("candidates").getJSONObject(0).getJSONObject("content").getJSONArray("parts");
            for(int i=0;i<parts.length();i++) if(parts.getJSONObject(i).has("text")) answer.append(parts.getJSONObject(i).getString("text"));
            String a=answer.toString().trim(); if(a.isEmpty()) a="No text response returned.";
            runOnUiThread(()->{
                latestAnswer=a; addBubble("PMAX",a,false); appendHistory("model",a);
                status.setText("Ready"); busy=false; sendBtn.setEnabled(true);
            });
        } catch(Exception e) {
            String msg=e.getMessage()==null?e.toString():e.getMessage();
            runOnUiThread(()->{
                addBubble("PMAX • ERROR",msg,false); status.setText("Request failed");
                busy=false; sendBtn.setEnabled(true);
            });
        } finally { if(c!=null)c.disconnect(); }
    }

    void speakLatest() {
        if(latestAnswer==null || latestAnswer.trim().isEmpty()) {
            status.setText("No Pmax answer to speak yet."); return;
        }
        if(tts!=null) {
            tts.setLanguage(Locale.getDefault());
            tts.speak(latestAnswer,TextToSpeech.QUEUE_FLUSH,null,"pmax_answer");
            status.setText("Speaking latest answer. Voice/language support depends on your device.");
        }
    }
    String read(InputStream in)throws Exception {
        if(in==null)return ""; ByteArrayOutputStream b=new ByteArrayOutputStream(); byte[] x=new byte[4096]; int n;
        while((n=in.read(x))!=-1)b.write(x,0,n); return b.toString("UTF-8");
    }
    void startVoice() {
        if(!SpeechRecognizer.isRecognitionAvailable(this)) { status.setText("Speech recognition is not available on this device."); return; }
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},REQ_MIC); return;
        }
        Intent i=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE,"my-MM");
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE,"my-MM");
        i.putExtra(RecognizerIntent.EXTRA_PROMPT,"Speak to Pmax");
        try { startActivityForResult(i,REQ_MIC); } catch(Exception e) { status.setText("Voice input could not start."); }
    }
    @Override protected void onActivityResult(int req,int res,Intent data) {
        super.onActivityResult(req,res,data);
        if(req==REQ_MIC && res==RESULT_OK && data!=null) {
            ArrayList<String> matches=data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if(matches!=null&&!matches.isEmpty()) { input.setText(matches.get(0)); input.setSelection(input.length()); }
        }
    }
    @Override public void onRequestPermissionsResult(int r,String[] p,int[] g) {
        super.onRequestPermissionsResult(r,p,g);
        if(r==REQ_MIC && g.length>0 && g[0]==PackageManager.PERMISSION_GRANTED) startVoice();
        else status.setText("Microphone permission is needed for voice input.");
    }
    @Override protected void onDestroy() {
        if(tts!=null) { tts.stop(); tts.shutdown(); }
        super.onDestroy();
    }
}

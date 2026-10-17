package local.aioffice.phone;

import android.content.Context;
import android.content.SharedPreferences;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;
import org.json.*;

final class Store {
    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("private-connection",0); }
    static String hash(String text) {
        try {
            byte[] raw=MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
            StringBuilder b=new StringBuilder();for(byte item:raw)b.append(String.format("%02x",item&255));return b.toString();
        } catch(Exception error) { throw new IllegalStateException("digest unavailable"); }
    }
    static synchronized String device(Context c) {
        String id=prefs(c).getString("device_id","");
        if(id.isEmpty()){id=UUID.randomUUID().toString().replace("-","");prefs(c).edit().putString("device_id",id).commit();}return id;
    }
    static void status(Context c,String text) {prefs(c).edit().putString("status",text).apply();}
    static synchronized JSONArray queue(Context c,String name) {
        try{return new JSONArray(prefs(c).getString(name,"[]"));}catch(JSONException error){return new JSONArray();}
    }
    static synchronized void add(Context c,String name,JSONObject value,String identity) {
        JSONArray old=queue(c,name),next=new JSONArray();String id=value.optString(identity);
        for(int i=Math.max(0,old.length()-148);i<old.length();i++){
            JSONObject row=old.optJSONObject(i);if(row!=null&&!id.equals(row.optString(identity)))next.put(row);
        }
        next.put(value);prefs(c).edit().putString(name,next.toString()).commit();
    }
    static synchronized void delivered(Context c,String name,JSONArray sent,String identity) {
        java.util.HashSet<String> ids=new java.util.HashSet<>();
        for(int i=0;i<sent.length();i++)ids.add(sent.optJSONObject(i).optString(identity));
        JSONArray current=queue(c,name),next=new JSONArray();
        for(int i=0;i<current.length();i++){JSONObject row=current.optJSONObject(i);if(row!=null&&!ids.contains(row.optString(identity)))next.put(row);}
        prefs(c).edit().putString(name,next.toString()).commit();
    }
}

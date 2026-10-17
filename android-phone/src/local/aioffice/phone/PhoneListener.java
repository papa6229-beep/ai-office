package local.aioffice.phone;

import android.app.Notification;
import android.os.Bundle;
import android.os.Parcelable;
import android.service.notification.*;
import org.json.*;

public class PhoneListener extends NotificationListenerService {
    static volatile PhoneListener current;
    @Override public void onListenerConnected(){current=this;BridgeService.start(this);}
    @Override public void onListenerDisconnected(){current=null;Store.status(this,"알림 접근 연결 대기 · 앱에서 권한을 확인해 주세요.");}
    @Override public void onDestroy(){if(current==this)current=null;super.onDestroy();}
    @Override public void onNotificationPosted(StatusBarNotification sbn){
        if(!Store.prefs(this).getBoolean("enabled",false)||Store.prefs(this).getString("token","").isEmpty())return;
        if((sbn.getNotification().flags&Notification.FLAG_GROUP_SUMMARY)!=0){
            new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(()->{
                try{for(StatusBarNotification row:getActiveNotifications())if(row.getKey().equals(sbn.getKey())){
                    JSONObject value=describe(row);if(value!=null)Store.add(this,"events",value,"revision");break;
                }}catch(Exception ignored){}
            },700);return;
        }
        JSONObject value=describe(sbn);if(value!=null)Store.add(this,"events",value,"revision");
    }
    static String clip(Object value,int limit){
        String text=value==null?"":value.toString();
        if(text.length()<=limit)return text;
        if(Character.isHighSurrogate(text.charAt(limit-1)))limit--;
        return text.substring(0,limit);
    }
    JSONObject describe(StatusBarNotification sbn){
        if(sbn.getPackageName().equals(getPackageName()))return null;
        if((sbn.getNotification().flags&Notification.FLAG_GROUP_SUMMARY)!=0){
            try{for(StatusBarNotification child:getActiveNotifications()){
                if(child.getPackageName().equals(sbn.getPackageName())&&child.getGroupKey().equals(sbn.getGroupKey())&&(child.getNotification().flags&Notification.FLAG_GROUP_SUMMARY)==0)return null;
            }}catch(Exception error){return null;}
        }
        try {
            Notification n=sbn.getNotification();Bundle e=n.extras;
            String source=sbn.getPackageName();
            try{source=getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(source,0)).toString();}catch(Exception ignored){}
            String title=clip(e.getCharSequence(Notification.EXTRA_TITLE),400);
            String body=clip(e.getCharSequence(Notification.EXTRA_BIG_TEXT),4000);
            if(body.isEmpty())body=clip(e.getCharSequence(Notification.EXTRA_TEXT),4000);
            Parcelable[] messages=e.getParcelableArray(Notification.EXTRA_MESSAGES);
            if(messages!=null&&messages.length>0){
                java.util.List<Notification.MessagingStyle.Message> parsed=Notification.MessagingStyle.Message.getMessagesFromBundleArray(messages);
                if(!parsed.isEmpty()){Notification.MessagingStyle.Message latest=parsed.get(parsed.size()-1);body=clip(latest.getText(),4000);}
            }
            if(title.isEmpty()&&body.isEmpty())body="Android가 이 알림의 내용을 제공하지 않았어요.";
            String key=Store.hash(sbn.getKey());
            String revision=Store.hash(key+"|"+sbn.getPostTime()+"|"+title+"|"+body);
            return new JSONObject().put("key",key).put("revision",revision).put("source",clip(source,160)).put("title",title).put("body",body).put("posted",sbn.getPostTime());
        }catch(Exception error){return null;}
    }
    synchronized JSONObject inventory()throws Exception {
        StatusBarNotification[] active=getActiveNotifications();if(active==null)throw new IllegalStateException("no inventory");
        JSONArray keys=new JSONArray(),items=new JSONArray();boolean complete=true;
        for(StatusBarNotification row:active){
            JSONObject item=describe(row);if(item==null)continue;
            if(items.length()>=150){complete=false;break;}items.put(item);keys.put(item.getString("key"));
        }
        return new JSONObject().put("notifications",items).put("active_keys",keys).put("complete",complete);
    }
    String dismiss(String key,String revision){
        try {
            for(StatusBarNotification row:getActiveNotifications()){
                JSONObject item=describe(row);if(item==null||!key.equals(item.optString("key")))continue;
                if(!revision.equals(item.optString("revision")))return "not_present";
                if(!row.isClearable())return "unsupported";
                cancelNotification(row.getKey());
                // Cancellation is asynchronous. Require a subsequent actual inventory.
                for(int i=0;i<10;i++){
                    Thread.sleep(100);boolean found=false;
                    for(StatusBarNotification next:getActiveNotifications())if(row.getKey().equals(next.getKey())){found=true;break;}
                    if(!found)return "dismissed";
                }
                return "unavailable";
            }
            return "not_present";
        }catch(Exception error){return "unavailable";}
    }
}

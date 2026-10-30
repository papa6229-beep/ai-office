package local.aioffice.phone;

import android.app.Notification;
import android.os.Bundle;
import android.os.Parcelable;
import android.app.RemoteInput;
import android.content.Intent;
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
    static boolean messenger(String source){
        String s=source.toLowerCase(java.util.Locale.ROOT);
        return s.contains("nate")||s.contains("네이트")||s.contains("hanbiro")||s.contains("한비로")||s.contains("between")||s.contains("비트윈")||s.contains("kakao")||s.contains("카카오");
    }
    static Notification.Action replyAction(Notification n){
        if(n.actions==null)return null;
        for(Notification.Action action:n.actions){
            RemoteInput[] inputs=action.getRemoteInputs();
            if(action.actionIntent==null||inputs==null)continue;
            for(RemoteInput input:inputs)if(input.getAllowFreeFormInput())return action;
        }
        return null;
    }
    static JSONObject conversation(StatusBarNotification sbn)throws Exception{
        Notification n=sbn.getNotification();Bundle e=n.extras;
        String title=clip(e.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE),400);
        if(title.isEmpty())title=clip(e.getCharSequence(Notification.EXTRA_TITLE),400);
        String identity="title|"+title,kind="notification",shortcut=n.getShortcutId();
        if(shortcut!=null&&!shortcut.isEmpty()){identity="shortcut|"+shortcut;kind="shortcut";}
        else if(android.os.Build.VERSION.SDK_INT>=28&&!e.getBoolean("android.isGroupConversation",false)){
            Parcelable[] bundles=e.getParcelableArray(Notification.EXTRA_MESSAGES);
            if(bundles!=null){
                java.util.List<Notification.MessagingStyle.Message> messages=Notification.MessagingStyle.Message.getMessagesFromBundleArray(bundles);
                for(int i=messages.size()-1;i>=0;i--){
                    android.app.Person person=messages.get(i).getSenderPerson();if(person==null)continue;
                    String stable=person.getKey()!=null?person.getKey():person.getUri();
                    if(stable!=null&&!stable.isEmpty()){identity="person|"+stable;kind="person";}
                    if(person.getName()!=null&&!person.getName().toString().isEmpty())title=clip(person.getName(),400);
                    if(kind.equals("notification"))identity="title|"+title;
                    break;
                }
            }
        }
        return new JSONObject().put("conversation",Store.hash(sbn.getPackageName()+"|"+identity)).put("conversation_kind",kind).put("conversation_title",title);
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
            if(sbn.getPackageName().equals("Uxpp.UC"))source="네이트온";
            else if(sbn.getPackageName().equals("com.hanbiro.globalmessenger_v2"))source="Hanbiro Talk";
            else if(sbn.getPackageName().equals("kr.co.vcnc.android.couple"))source="Between";
            else if(sbn.getPackageName().equals("com.kakao.talk"))source="카카오톡";
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
            JSONObject room=conversation(sbn);
            boolean hasReply=messenger(source)&&replyAction(n)!=null,unambiguous=true;
            if(room.getString("conversation_kind").equals("notification")){
                for(StatusBarNotification other:getActiveNotifications()){
                    if(other.getKey().equals(sbn.getKey())||!other.getPackageName().equals(sbn.getPackageName())||(other.getNotification().flags&Notification.FLAG_GROUP_SUMMARY)!=0)continue;
                    if(conversation(other).getString("conversation").equals(room.getString("conversation"))){unambiguous=false;break;}
                }
            }
            JSONArray history=new JSONArray();
            if(messages!=null){
                java.util.List<Notification.MessagingStyle.Message> parsed=Notification.MessagingStyle.Message.getMessagesFromBundleArray(messages);
                for(int i=Math.max(0,parsed.size()-30);i<parsed.size();i++){
                    Notification.MessagingStyle.Message message=parsed.get(i);
                    String sender=clip(message.getSender(),400);
                    history.put(new JSONObject().put("text",clip(message.getText(),4000)).put("sender",sender)
                        .put("posted",message.getTimestamp()>0?message.getTimestamp():sbn.getPostTime()).put("direction",message.getSender()==null?"out":"in"));
                }
            }
            if(history.length()==0)history.put(new JSONObject().put("text",body).put("sender",title).put("posted",sbn.getPostTime()).put("direction","in"));
            return new JSONObject().put("key",key).put("revision",revision).put("source",clip(source,160)).put("title",title).put("body",body).put("posted",sbn.getPostTime())
                .put("package",sbn.getPackageName()).put("conversation",room.getString("conversation")).put("conversation_kind",room.getString("conversation_kind"))
                .put("conversation_title",room.getString("conversation_title")).put("messages",history).put("reply_action",hasReply).put("can_reply",hasReply&&unambiguous);
        }catch(Exception error){return null;}
    }
    synchronized String reply(JSONObject command){
        String id=command.optString("id"),record="reply-result-"+id;
        android.content.SharedPreferences prefs=Store.prefs(this);
        if(prefs.contains(record))return prefs.getString(record,"unknown");
        String result="not_present";
        try{
            if(command.getDouble("expires")*1000<System.currentTimeMillis())return "expired";
            String text=command.getString("text");
            if(text.trim().isEmpty()||text.length()>2000)return "failed";
            for(StatusBarNotification row:getActiveNotifications()){
                JSONObject item=describe(row);
                if(item==null||!command.getString("key").equals(item.optString("key")))continue;
                if(!command.getString("conversation").equals(item.optString("conversation"))||!command.getString("revision").equals(item.optString("revision")))return "stale";
                if(!item.optBoolean("can_reply"))return "unsupported";
                Notification.Action action=replyAction(row.getNotification());
                if(action==null)return "unsupported";
                if(android.os.Build.VERSION.SDK_INT>=31&&action.isAuthenticationRequired()&&getSystemService(android.app.KeyguardManager.class).isDeviceLocked())return "needs_unlock";
                // Persist before dispatch. A crash at this boundary must not resend.
                if(!prefs.edit().putString(record,"unknown").commit())return "failed";
                Bundle results=new Bundle();
                for(RemoteInput input:action.getRemoteInputs())if(input.getAllowFreeFormInput())results.putCharSequence(input.getResultKey(),text);
                Intent intent=new Intent();RemoteInput.addResultsToIntent(action.getRemoteInputs(),intent,results);
                if(android.os.Build.VERSION.SDK_INT>=28)RemoteInput.setResultsSource(intent,RemoteInput.SOURCE_FREE_FORM_INPUT);
                action.actionIntent.send(this,0,intent);
                result="accepted";break;
            }
        }catch(android.app.PendingIntent.CanceledException error){result="stale";}
        catch(Exception error){result="failed";}
        prefs.edit().putString(record,result).commit();
        return result;
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

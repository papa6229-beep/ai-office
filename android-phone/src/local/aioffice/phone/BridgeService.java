package local.aioffice.phone;

import android.app.*;
import android.content.*;
import android.os.*;
import org.json.*;

public class BridgeService extends Service {
    private volatile boolean running;
    private Thread worker;
    static void start(Context c){
        if(!Store.prefs(c).getBoolean("enabled",false)||Store.prefs(c).getString("token","").isEmpty())return;
        try{c.startForegroundService(new Intent(c,BridgeService.class));}
        catch(Exception error){Store.status(c,"앱에서 연결 시작을 눌러 주세요. 백그라운드 연결 권한을 확인해 주세요.");}
    }
    @Override public void onCreate(){
        super.onCreate();
        NotificationManager manager=getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel("connection","PC 연결 상태",NotificationManager.IMPORTANCE_LOW));
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE);
        Notification note=new Notification.Builder(this,"connection").setContentTitle("에이메스 연결")
            .setContentText("같은 네트워크의 PC로 알림을 공유하고 있어요.").setSmallIcon(android.R.drawable.ic_dialog_info).setOngoing(true).setContentIntent(open).build();
        startForeground(7,note);running=true;
        worker=new Thread(this::loop,"phone-direct-sync");worker.start();
    }
    private void loop(){
        while(running){
            String usedToken="";
            try {
                android.content.SharedPreferences prefs=Store.prefs(this);
                String token=prefs.getString("token","");if(token.isEmpty()||!prefs.getBoolean("enabled",false)){stopSelf();return;}
                PhoneListener listener=PhoneListener.current;
                if(listener==null){Store.status(this,"알림 접근 허용이 필요해요.");Thread.sleep(3000);continue;}
                JSONArray events=Store.queue(this,"events"),acks=Store.queue(this,"acks");
                JSONObject state=listener.inventory();JSONArray notifications=state.getJSONArray("notifications");
                java.util.HashSet<String> revisions=new java.util.HashSet<>();
                for(int i=0;i<notifications.length();i++)revisions.add(notifications.getJSONObject(i).getString("revision"));
                // Oldest queued revisions first so the current snapshot wins.
                JSONArray combined=new JSONArray();int space=Math.max(0,150-notifications.length());
                for(int i=Math.max(0,events.length()-space);i<events.length();i++){
                    JSONObject item=events.getJSONObject(i);if(!revisions.contains(item.getString("revision")))combined.put(item);
                }
                for(int i=0;i<notifications.length();i++)combined.put(notifications.getJSONObject(i));
                long sequence=prefs.getLong("sequence",0)+1;prefs.edit().putLong("sequence",sequence).commit();
                state.put("notifications",combined).put("protocol",1).put("sequence",sequence).put("acks",acks);
                usedToken=token;
                JSONObject response=Transport.request(prefs.getString("host",""),prefs.getString("pin",""),"/v1/sync",token,state);
                Store.delivered(this,"events",combined,"revision");Store.delivered(this,"acks",acks,"id");
                JSONArray commands=response.getJSONArray("commands");
                for(int i=0;i<commands.length();i++){
                    JSONObject command=commands.getJSONObject(i);String result=listener.dismiss(command.getString("key"),command.getString("revision"));
                    Store.add(this,"acks",new JSONObject().put("id",command.getString("id")).put("state",result),"id");
                }
                Store.status(this,"PC 직접 연결됨 · 알림 "+notifications.length()+"개 동기화 중");
                Thread.sleep(commands.length()>0?300:3000);
            }catch(InterruptedException error){return;}
            catch(SecurityException error){
                if(!usedToken.isEmpty()&&!usedToken.equals(Store.prefs(this).getString("token","")))continue;
                Store.status(this,"PC에서 연결이 해제됐어요. QR을 다시 읽어 주세요.");Store.prefs(this).edit().putBoolean("enabled",false).apply();stopSelf();return;
            }
            catch(Exception error){
                Store.status(this,"PC 응답 대기 · Wi-Fi·오피스 실행·방화벽을 확인해 주세요. 자동 재시도 중이에요.");
                try{Thread.sleep(5000);}catch(InterruptedException stop){return;}
            }
        }
    }
    @Override public int onStartCommand(Intent intent,int flags,int id){return START_STICKY;}
    @Override public void onDestroy(){running=false;if(worker!=null)worker.interrupt();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}

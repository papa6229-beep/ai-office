package local.aioffice.phone;

import android.Manifest;
import android.app.*;
import android.companion.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;

public class MainActivity extends Activity {
    private TextView state;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final Runnable refresh=new Runnable(){public void run(){
        state.setText(Store.prefs(MainActivity.this).getString("status","PC의 알림 → 휴대폰 연결에서 QR을 열어 주세요."));handler.postDelayed(this,2000);
    }};
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);getWindow().setStatusBarColor(0xff17191b);getWindow().setNavigationBarColor(0xff17191b);
        LinearLayout layout=new LinearLayout(this);layout.setOrientation(1);layout.setPadding(28,48,28,28);layout.setBackgroundColor(0xff17191b);
        ScrollView scroll=new ScrollView(this);scroll.addView(layout);setContentView(scroll);
        TextView title=new TextView(this);title.setText("에이메스 연결");title.setTextSize(28);title.setTextColor(0xffafd94b);layout.addView(title);
        TextView info=new TextView(this);info.setText("PC는 랜선, 휴대폰은 같은 네트워크 Wi-Fi에 연결해 주세요.\n알림을 에이메스에 직접 전달해요. 답장·파일·화면 원격 제어 기능은 없어요.");info.setTextColor(0xffdddddd);info.setPadding(0,20,0,20);layout.addView(info);
        state=new TextView(this);state.setTextColor(0xffffc4d5);state.setPadding(0,8,0,20);layout.addView(state);
        button(layout,"1. PC의 QR 읽기",()->{
            if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.CAMERA},10);
            else startActivityForResult(new Intent(this,ScanActivity.class),20);
        });
        button(layout,"2. 알림 접근 허용",()->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        button(layout,"3. 연결한 PC를 기기로 승인",this::associate);
        button(layout,"4. 백그라운드 연결 허용",()->{
            try{startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,Uri.parse("package:"+getPackageName())));}
            catch(Exception error){startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}
        });
        button(layout,"연결 시작",()->{
            Store.prefs(this).edit().putBoolean("enabled",true).apply();
            if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},11);
            BridgeService.start(this);
        });
        button(layout,"휴대폰에서 연결 해제",()->new AlertDialog.Builder(this).setMessage("PC 연결을 해제할까요? 다시 연결할 때 QR이 필요해요.").setNegativeButton("취소",null).setPositiveButton("연결 해제",(d,w)->{
            Store.prefs(this).edit().putBoolean("enabled",false).remove("token").remove("events").remove("acks").apply();stopService(new Intent(this,BridgeService.class));
            if(Build.VERSION.SDK_INT>=33){int id=Store.prefs(this).getInt("association",-1);if(id>=0)try{getSystemService(CompanionDeviceManager.class).disassociate(id);}catch(Exception ignored){}}
            Store.prefs(this).edit().remove("association").apply();Store.status(this,"연결 해제됨");
        }).show());
        TextView note=new TextView(this);note.setText("알림 접근이 막히면 앱 정보 → 오른쪽 위 메뉴 → 제한된 설정 허용을 확인해 주세요.\n\n기기 승인과 알림 접근을 허용해도 Android 정책에 따라 인증문자 등 민감한 내용은 가려질 수 있어요. 실제 수신 결과로 확인해요.\n\n휴대폰에서 지울 수 없는 알림은 오피스에서도 정리할 수 없어요. 알림 정리는 메신저 대화 읽음과 달라요.");note.setTextColor(0xffaeb2b6);note.setPadding(0,20,0,10);layout.addView(note);
    }
    private void button(LinearLayout layout,String text,Runnable action){
        Button b=new Button(this);b.setText(text);b.setAllCaps(false);b.setOnClickListener(v->action.run());layout.addView(b,new LinearLayout.LayoutParams(-1,-2));
    }
    private void associate(){
        if(Store.prefs(this).getString("token","").isEmpty()){Toast.makeText(this,"먼저 PC의 QR을 읽어 주세요.",Toast.LENGTH_LONG).show();return;}
        if(Build.VERSION.SDK_INT<33){Toast.makeText(this,"이 Android 버전에서는 기기 승인 없이 알림 접근을 사용해요.",Toast.LENGTH_LONG).show();return;}
        CompanionDeviceManager manager=getSystemService(CompanionDeviceManager.class);
        int existing=Store.prefs(this).getInt("association",-1);
        if(existing>=0){Toast.makeText(this,"이미 승인한 PC예요.",Toast.LENGTH_SHORT).show();return;}
        try {
            AssociationRequest request=new AssociationRequest.Builder().setSelfManaged(true).setDisplayName("AI OFFICE PC").build();
            manager.associate(request,getMainExecutor(),new CompanionDeviceManager.Callback(){
                @Override public void onAssociationPending(IntentSender sender){try{startIntentSenderForResult(sender,30,null,0,0,0);}catch(Exception error){Store.status(MainActivity.this,"기기 승인 화면을 열지 못했어요.");}}
                @Override public void onAssociationCreated(AssociationInfo info){Store.prefs(MainActivity.this).edit().putInt("association",info.getId()).apply();Store.status(MainActivity.this,"연결한 PC의 기기 승인을 받았어요.");BridgeService.start(MainActivity.this);}
                @Override public void onFailure(CharSequence error){Store.status(MainActivity.this,"기기 승인을 완료하지 못했어요. 알림 접근 상태를 확인해 주세요.");}
            });
        }catch(Exception error){Store.status(this,"이 기기에서 연결 기기 승인을 지원하지 않아요. 알림 접근 권한으로 수신해요.");}
    }
    @Override public void onRequestPermissionsResult(int request,String[] permissions,int[] grants){
        super.onRequestPermissionsResult(request,permissions,grants);
        if(request==10&&grants.length>0&&grants[0]==PackageManager.PERMISSION_GRANTED)startActivityForResult(new Intent(this,ScanActivity.class),20);
    }
    @Override protected void onActivityResult(int request,int result,Intent data){
        super.onActivityResult(request,result,data);
        if(request==20&&result==RESULT_OK&&data!=null){
            String value=data.getStringExtra("qr");Store.status(this,"PC 연결을 확인하고 있어요.");
            new Thread(()->{
                try{Transport.pair(this,value);BridgeService.start(this);}
                catch(Exception error){Store.status(this,"QR 연결 실패 · PC와 같은 네트워크인지 확인하고 QR을 새로 읽어 주세요.");}
            },"pair-pc").start();
        }
    }
    @Override protected void onResume(){super.onResume();handler.post(refresh);}
    @Override protected void onPause(){handler.removeCallbacks(refresh);super.onPause();}
}

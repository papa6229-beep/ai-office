package local.aioffice.phone;
import android.app.Activity;
import android.content.Intent;
import android.hardware.Camera;
import android.os.Bundle;
import android.view.*;
import android.widget.*;
import com.google.zxing.*;
import com.google.zxing.common.HybridBinarizer;
import java.util.*;

@SuppressWarnings("deprecation")
public class ScanActivity extends Activity implements SurfaceHolder.Callback,Camera.PreviewCallback {
    private Camera camera;private boolean done;private int frames;
    @Override public void onCreate(Bundle saved){
        super.onCreate(saved);LinearLayout layout=new LinearLayout(this);layout.setOrientation(1);
        TextView title=new TextView(this);title.setText("PC의 에이메스 연결 QR을 비춰 주세요.");title.setPadding(20,24,20,24);layout.addView(title);
        SurfaceView surface=new SurfaceView(this);layout.addView(surface,new LinearLayout.LayoutParams(-1,0,1));surface.getHolder().addCallback(this);setContentView(layout);
    }
    @Override public void surfaceCreated(SurfaceHolder holder){
        try{camera=Camera.open();camera.setDisplayOrientation(90);camera.setPreviewDisplay(holder);
            Camera.Parameters p=camera.getParameters();
            if(p.getSupportedFocusModes().contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE))p.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            for(Camera.Size size:p.getSupportedPreviewSizes())if(size.width==1280&&size.height==720){p.setPreviewSize(size.width,size.height);break;}
            camera.setParameters(p);camera.setPreviewCallback(this);camera.startPreview();
        }catch(Exception error){Toast.makeText(this,"카메라를 열지 못했어요.",Toast.LENGTH_LONG).show();finish();}
    }
    @Override public void onPreviewFrame(byte[] data,Camera source){
        if(done||frames++%6!=0)return;
        try{Camera.Size size=source.getParameters().getPreviewSize();
            PlanarYUVLuminanceSource pixels=new PlanarYUVLuminanceSource(data,size.width,size.height,0,0,size.width,size.height,false);
            MultiFormatReader reader=new MultiFormatReader();Map<DecodeHintType,Object> hints=new EnumMap<>(DecodeHintType.class);
            hints.put(DecodeHintType.POSSIBLE_FORMATS,Collections.singletonList(BarcodeFormat.QR_CODE));
            String value=reader.decode(new BinaryBitmap(new HybridBinarizer(pixels)),hints).getText();
            if(!value.startsWith("aemeath://pair?"))return;done=true;setResult(RESULT_OK,new Intent().putExtra("qr",value));finish();
        }catch(Exception ignored){}
    }
    private void release(){if(camera!=null){camera.setPreviewCallback(null);camera.stopPreview();camera.release();camera=null;}}
    @Override public void surfaceChanged(SurfaceHolder holder,int format,int width,int height){}
    @Override public void surfaceDestroyed(SurfaceHolder holder){release();}
    @Override protected void onPause(){release();super.onPause();}
}

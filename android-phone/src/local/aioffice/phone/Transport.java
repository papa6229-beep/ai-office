package local.aioffice.phone;

import android.content.Context;
import android.net.Uri;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.io.*;
import java.security.MessageDigest;
import java.security.cert.*;
import javax.net.ssl.*;
import org.json.*;

final class Transport {
    static JSONObject request(String host,String pin,String path,String token,JSONObject body) throws Exception {
        if(!host.matches("(?:10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}|192\\.168\\.\\d{1,3}\\.\\d{1,3}|172\\.(?:1[6-9]|2\\d|3[01])\\.\\d{1,3}\\.\\d{1,3})")||!pin.matches("[0-9a-f]{64}"))throw new IOException("invalid local connection");
        TrustManager[] trust={new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String type)throws CertificateException{throw new CertificateException();}
            public void checkServerTrusted(X509Certificate[] chain,String type)throws CertificateException{
                try {
                    if(chain.length==0)throw new CertificateException();chain[0].checkValidity();
                    byte[] raw=MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
                    StringBuilder actual=new StringBuilder();for(byte b:raw)actual.append(String.format("%02x",b&255));
                    if(!MessageDigest.isEqual(actual.toString().getBytes(StandardCharsets.US_ASCII),pin.getBytes(StandardCharsets.US_ASCII)))throw new CertificateException("PC certificate changed");
                }catch(CertificateException error){throw error;}catch(Exception error){throw new CertificateException("PC certificate not verified");}
            }
        }};
        SSLContext ssl=SSLContext.getInstance("TLS");ssl.init(null,trust,null);
        HttpsURLConnection connection=(HttpsURLConnection)new URL("https://"+host+":8766"+path).openConnection();
        connection.setSSLSocketFactory(ssl.getSocketFactory());
        // The QR-pinned certificate authenticates this exact PC, including its private IP.
        connection.setHostnameVerifier((hostname,session)->hostname.equals(host));
        connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(10000);connection.setReadTimeout(15000);
        connection.setRequestMethod("POST");connection.setRequestProperty("Content-Type","application/json; charset=utf-8");
        if(!token.isEmpty())connection.setRequestProperty("Authorization","Bearer "+token);
        byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);connection.setFixedLengthStreamingMode(bytes.length);connection.setDoOutput(true);
        try {
            try(OutputStream stream=connection.getOutputStream()){stream.write(bytes);}
            int status=connection.getResponseCode();if(status==403)throw new SecurityException("QR 연결을 다시 해 주세요.");
            if(status!=200)throw new IOException("receiver unavailable");
            ByteArrayOutputStream output=new ByteArrayOutputStream();
            try(InputStream input=connection.getInputStream()){
                byte[] buffer=new byte[4096];int n;while((n=input.read(buffer))!=-1){output.write(buffer,0,n);if(output.size()>262144)throw new IOException("response limit");}
            }
            return new JSONObject(output.toString("UTF-8"));
        } finally {connection.disconnect();}
    }
    static void pair(Context context,String qr)throws Exception {
        Uri uri=Uri.parse(qr);
        if(!"aemeath".equals(uri.getScheme())||!"pair".equals(uri.getHost())||!"1".equals(uri.getQueryParameter("v"))||!"8766".equals(uri.getQueryParameter("port")))throw new IOException("에이메스 연결 QR을 읽어 주세요.");
        String host=uri.getQueryParameter("host"),pin=uri.getQueryParameter("pin"),secret=uri.getQueryParameter("secret");
        if(host==null||pin==null||secret==null||!secret.matches("[0-9a-f]{64}"))throw new IOException("invalid QR");
        JSONObject body=new JSONObject().put("device_id",Store.device(context)).put("name",android.os.Build.MODEL).put("secret",secret);
        JSONObject result=request(host,pin,"/v1/pair","",body);String token=result.getString("token");
        if(!token.matches("[0-9a-f]{32}\\.[0-9a-f]{64}"))throw new IOException("invalid pairing receipt");
        Store.prefs(context).edit().putString("host",host).putString("pin",pin).putString("token",token).putBoolean("enabled",true).commit();
        Store.status(context,"PC 연결 등록됨 · 알림 접근을 허용해 주세요.");
    }
}

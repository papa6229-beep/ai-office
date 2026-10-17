"""Build the local companion APK without Gradle, tests, or model calls.

Official Google platform/build-tools archives and ZXing Core must be prepared in
.provider_runtime/android-build. All keys and generated files stay there.
"""
from pathlib import Path
import os
import secrets
import shutil
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent
WORK=ROOT.parent/'.provider_runtime'/'android-build'


def main():
    java=Path(shutil.which('java')).resolve();jdk=java.parent
    platform=next((WORK/'platform').glob('*/android.jar'))
    tools=next(path.parent for path in (WORK/'tools').glob('*/aapt2.exe'))
    qr=WORK/'zxing-core.jar'
    signing=WORK/'signing';signing.mkdir(exist_ok=True)
    password=signing/'password.txt';key=signing/'phone.p12'
    if not password.exists():password.write_text(secrets.token_hex(32),encoding='ascii')
    def run(argv):
        result=subprocess.run([str(value) for value in argv],capture_output=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.stdout:print(result.stdout[-5000:])
        if result.stderr:print(result.stderr[-5000:])
        if result.returncode:raise RuntimeError('Build step failed: '+Path(str(argv[0])).name)
    if not key.exists():
        run([jdk/'keytool.exe','-genkeypair','-keystore',key,'-storetype','PKCS12','-alias','aemeath',
             '-keyalg','EC','-groupname','secp256r1','-validity','3650','-dname','CN=AI OFFICE Local Phone',
             '-storepass:file',password,'-keypass:file',password])
    with tempfile.TemporaryDirectory(prefix='compile-',dir=WORK) as name:
        folder=Path(name);classes=folder/'classes';classes.mkdir();dex=folder/'dex';dex.mkdir()
        sources=sorted((ROOT/'src').rglob('*.java'))
        run([jdk/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',str(platform)+os.pathsep+str(qr),'-d',classes,*sources])
        compiled=folder/'classes.jar'
        with zipfile.ZipFile(compiled,'w',zipfile.ZIP_DEFLATED) as archive:
            for item in classes.rglob('*.class'):archive.write(item,item.relative_to(classes).as_posix())
        run([java,'-cp',tools/'lib/d8.jar','com.android.tools.r8.D8','--lib',platform,'--min-api','26','--output',dex,compiled,qr])
        unsigned=folder/'unsigned.apk'
        run([tools/'aapt2.exe','link','-o',unsigned,'-I',platform,'--manifest',ROOT/'AndroidManifest.xml',
             '--min-sdk-version','26','--target-sdk-version','35','-A',ROOT/'assets'])
        with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as archive:
            for item in dex.glob('*.dex'):archive.write(item,item.name)
        aligned=folder/'aligned.apk';run([tools/'zipalign.exe','-f','4',unsigned,aligned])
        output=WORK/'aemeath-phone.apk'
        run([java,'-jar',tools/'lib/apksigner.jar','sign','--ks',key,'--ks-key-alias','aemeath',
             '--ks-pass','file:'+str(password),'--out',output,aligned])
        run([java,'-jar',tools/'lib/apksigner.jar','verify','--verbose',output])
        print('APK:',output,'bytes:',output.stat().st_size)


if __name__=='__main__':main()

"""Synchronize the selected native Hanbiro window; never send chat messages."""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import struct
import subprocess
import sys

SCRIPT = r"""
const fs=require('fs');
(async()=>{
 const input=JSON.parse(fs.readFileSync(0,'utf8'));
 const pages=await(await fetch(`http://127.0.0.1:${input.port}/json/list`,{signal:AbortSignal.timeout(1500)})).json();
 if(pages.some(p=>p.url.includes('unlock.html')))throw Error('한비로 잠금을 먼저 해제해 주세요.');
 const matches=pages.filter(p=>p.type==='page'&&p.url.replaceAll('\\','/').toLowerCase().includes('/hanbiro-talk/resources/app.asar/dist/renderer/index.html')&&p.url.includes('#/main'));
 if(matches.length!==1)throw Error('한비로 기본 화면을 하나로 확인하지 못했어요.');
 const ws=new WebSocket(matches[0].webSocketDebuggerUrl);let seq=0;const pending=new Map();
 await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('연결 시간 초과')),1200);ws.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('연결 실패'));},{once:true});});
 ws.addEventListener('message',event=>{const r=JSON.parse(event.data),p=pending.get(r.id);if(!p)return;pending.delete(r.id);clearTimeout(p.timer);if(r.error||r.result?.exceptionDetails)p.reject(Error('표시 상태 확인 실패'));else p.resolve(r.result.result.value);});
 function evaluate(expression){return new Promise((resolve,reject)=>{const id=++seq,timer=setTimeout(()=>{pending.delete(id);reject(Error('표시 응답 시간 초과'));},1200);pending.set(id,{resolve,reject,timer});ws.send(JSON.stringify({id,method:'Runtime.evaluate',params:{expression,returnByValue:true}}));});}
 try{
  const expression='({visible:document.visibilityState==="visible",width:innerWidth,height:innerHeight,ready:document.readyState==="complete"})';
  const before=await evaluate(expression);let after=before,repaired=false;
  if(!before.visible&&input.action==='present'){
   // Installed Hanbiro 2.1.257 preload/main: its normal BrowserWindow show/restore.
   // Only the host's explicit, foreground user selection permits this action.
   await evaluate('window.electron.ipcRenderer.sendMessage("main-ipc-channels","setStateMainWindow",{restore:true,show:true})');
   repaired=true;
   const until=Date.now()+1600;
   do{after=await evaluate(expression);if(after.visible&&after.ready)break;await new Promise(r=>setTimeout(r,50));}while(Date.now()<until);
  }
  console.log(JSON.stringify({ok:after.visible&&after.ready,observed:true,renderer_visible:after.visible,viewport:{width:after.width,height:after.height},repaired}));
 }finally{ws.close();}
})().catch(e=>{console.log(JSON.stringify({ok:false,error:e.message}));process.exitCode=1;});
"""


def presentation_settings():
    root = Path(__file__).resolve().parent
    app = Path.home() / 'AppData/Local/Programs/hanbiro-talk/HanbiroTalk2.exe'
    with (app.parent / 'resources/app.asar').open('rb') as stream:
        header = struct.unpack('<4I', stream.read(16))
        tree = json.loads(stream.read(header[3]))
        node = tree['files']['package.json']
        stream.seek(8 + header[1] + int(node['offset']))
        if json.loads(stream.read(node['size']))['version'] != '2.1.257':
            raise ValueError('업데이트된 한비로톡은 PC 원본 창에서 이용해 주세요. 통합 화면 연결을 확인 중이에요.')
    cfg = json.loads((root / '.provider_runtime/messenger/hanbiro-cdp.json').read_text('utf-8-sig'))
    port = cfg.get('port')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('기존 한비로 연결을 확인하세요.')
    return app, port


def process_path(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ValueError('원본 앱의 실행 경로를 확인하지 못했어요.')
    try:
        text = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(text))
        if not kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)):
            raise ValueError('원본 앱의 실행 경로를 확인하지 못했어요.')
        return Path(text.value).resolve()
    finally:
        kernel.CloseHandle(handle)


def run(value):
    if value.get('action') not in {'observe', 'present'}:
        raise ValueError('표시 확인 요청이 올바르지 않아요.')
    if any(type(value.get(key)) is not int or value[key] <= 0 for key in ('office', 'handle')):
        raise ValueError('표시 대상 창을 확인하세요.')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.IsWindow.argtypes = [w.HWND]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindow.argtypes = [w.HWND, w.UINT]
    user.GetWindow.restype = w.HWND
    user.GetForegroundWindow.restype = w.HWND
    office, handle = value['office'], value['handle']
    if not user.IsWindow(office) or not user.IsWindow(handle) or user.GetWindow(handle, 4) != office:
        raise ValueError('현재 통합창에 연결된 한비로만 표시할 수 있어요.')
    office_pid, app_pid = w.DWORD(), w.DWORD()
    user.GetWindowThreadProcessId(office, ctypes.byref(office_pid))
    user.GetWindowThreadProcessId(handle, ctypes.byref(app_pid))
    root = Path(__file__).resolve().parent
    expected_app, port = presentation_settings()
    if process_path(app_pid.value) != expected_app.resolve():
        raise ValueError('한비로 원본 앱의 경로가 달라요.')
    if process_path(office_pid.value) != (root / 'local_backups/desktop-app/bin/Aemeath.exe').resolve():
        raise ValueError('현재 에이메스 앱의 경로가 달라요.')
    if value['action'] == 'present' and user.GetForegroundWindow() not in {office, handle}:
        raise ValueError('다른 화면을 사용 중이라 한비로 표시를 시작하지 않았어요. 다시 선택해 주세요.')
    node = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    result = subprocess.run([str(node), '-e', SCRIPT], input=json.dumps({'port':port, 'action':value['action']}),
                            text=True, encoding='utf-8', capture_output=True, timeout=7,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    result_value = json.loads(result.stdout)
    if result.returncode or not result_value.get('ok'):
        if value['action'] == 'observe' and result_value.get('observed'):
            return result_value
        raise ValueError(result_value.get('error', '한비로 내부 화면이 아직 숨김 상태예요.'))
    return result_value


if __name__ == '__main__':
    try:
        raw = sys.stdin.read(2049)
        if len(raw) > 2048:
            raise ValueError('표시 요청이 너무 길어요.')
        print(json.dumps(run(json.loads(raw)), ensure_ascii=True))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '한비로 내부 표시 연결을 확인하지 못했어요.'
        print(json.dumps({'ok':False, 'error':message}, ensure_ascii=True))

"""Local-only Times Gate display transport; never calls a model or sends chat text."""
import base64
import ctypes
import io
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import shutil
from collections import OrderedDict
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'local_backups' / 'divoom'
CONFIG = DATA / 'config.json'
ORIGINS = {'http://127.0.0.1:8765', 'http://localhost:8765'}
PORT = 18766
ASSET_PORT = 18767
REVISION = '2026-10-29-visible-hq-reconnect-v15'


def store_key(ip, token):
    """Use a device-specific Credential Manager entry, separate from web logins."""
    import credential_vault as vault
    from ctypes import wintypes as w
    raw = ctypes.create_string_buffer(str(int(token)).encode('utf-16-le'))
    rec = vault._Credential()
    rec.Type = 1
    rec.TargetName = 'AI_OFFICE/device/divoom/' + ip
    rec.Comment = 'AI OFFICE Times Gate local device key'
    rec.CredentialBlobSize = len(raw.raw) - 1
    rec.CredentialBlob = ctypes.cast(raw, ctypes.POINTER(w.BYTE))
    rec.Persist = 2
    rec.UserName = 'Times Gate'
    try:
        if not vault._api().CredWriteW(ctypes.byref(rec), 0):
            raise RuntimeError('Device key could not be stored')
    finally:
        ctypes.memset(raw, 0, len(raw))


def load_key(ip):
    import credential_vault as vault
    api = vault._api()
    pointer = vault._Pointer()
    if not api.CredReadW('AI_OFFICE/device/divoom/' + ip, 1, 0, ctypes.byref(pointer)):
        raise RuntimeError('Device key is not configured')
    try:
        rec = pointer.contents
        return int(ctypes.string_at(rec.CredentialBlob, rec.CredentialBlobSize).decode('utf-16-le'))
    finally:
        api.CredFree(pointer)


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.03 * (attempt + 1))


class Display:
    def __init__(self):
        self.config = json.loads(CONFIG.read_text(encoding='utf-8'))
        self.asset_pack = self.config.get('asset_pack', '')
        self.asset_manifest = None
        if self.asset_pack:
            if not isinstance(self.asset_pack, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', self.asset_pack):
                raise ValueError('Invalid display artwork pack')
            self.asset_root = (DATA / 'asset-packs' / self.asset_pack).resolve()
            if not self.asset_root.is_relative_to(DATA.resolve()):
                raise ValueError('Artwork pack outside device data')
            self.asset_manifest = json.loads((self.asset_root / 'manifest.json').read_text(encoding='utf-8'))
        self.ip = self.config['ip']
        if self.ip != '192.168.0.16':
            raise ValueError('Device identity needs explicit reconfiguration')
        self.token = load_key(self.ip)
        self.nonce = secrets.token_urlsafe(32)
        self.command_lock = threading.Lock()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.desired = {}
        self.actual = {}
        self.last_heartbeat = 0
        self.last_error = ''
        self.next_attempt = 0
        self.cache = {}
        self.transmissions = 0
        self.panel_groups = {}
        for row in self.config['screens']:
            index = row['screen']
            if not isinstance(index, int) or index not in range(5):
                raise ValueError('Invalid display panel')
            self.panel_groups.setdefault(index, []).append(row)
        self.rotation_seconds = max(10, float(self.config.get('rotation_seconds', 12)))
        self.rotation_cursor = {index: 0 for index in self.panel_groups}
        self.rotation_due = {index: 0 for index in self.panel_groups}
        self.panel_actual = {}
        self.recent_presentations = []
        self.uploading = None
        self.asset_session = secrets.token_hex(6)
        self.continuous_cache = OrderedDict()
        self.continuous_signatures = {}
        self.continuous_candidates = {}
        self.continuous_updates = {}
        self.continuous_phase = {}
        self.observed_at = 0
        self.observation_source = None
        self.ignored_snapshots = 0
        self.activity_version = None
        self.continuous_ready_at = 0
        self.continuous_next_panel = 0
        self.asset_server = None
        self.asset_host = None
        self.asset_files = {}
        self.asset_served = {}
        self.asset_request_count = 0
        self.asset_last_request = None
        self.asset_condition = threading.Condition(self.lock)

    def start_asset_server(self):
        # Serve only approved GIF bytes, only to this LAN device. Never serve a directory.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((self.ip, 80))
            self.asset_host = route.getsockname()[0]
        display = self
        class Assets(BaseHTTPRequestHandler):
            def do_GET(self):
                with display.lock:
                    display.asset_request_count += 1
                    display.asset_last_request = {'at':time.time(),'peer_allowed':self.client_address[0]==display.ip,'host_allowed':self.headers.get('Host')==display.asset_host+':'+str(ASSET_PORT),'registered':self.path in display.asset_files}
                if (self.client_address[0] != display.ip
                        or self.headers.get('Host') != display.asset_host+':'+str(ASSET_PORT)):
                    self.send_error(404)
                    return
                with display.lock:
                    content = display.asset_files.get(self.path)
                if content is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/gif')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
                self.end_headers()
                try:
                    self.wfile.write(content)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                with display.asset_condition:
                    previous = display.asset_served.get(self.path, {})
                    display.asset_served[self.path] = {'count': previous.get('count', 0)+1,
                                                       'bytes': len(content), 'at': time.time()}
                    display.asset_condition.notify_all()
            def log_message(self, *args):
                pass
        self.asset_server = ThreadingHTTPServer((self.asset_host, ASSET_PORT), Assets)
        threading.Thread(target=self.asset_server.serve_forever, daemon=True,
                         name='times-gate-approved-gifs').start()
        for item in self.asset_manifest['files']:
            self.asset_url(item['asset'], item['state'])

    def asset_url(self, asset, state):
        if self.asset_manifest is None or self.asset_server is None:
            raise ValueError('Approved GIF file server is unavailable')
        item = next((row for row in self.asset_manifest['files']
                     if row.get('asset') == asset and row['state'] == state), None)
        if item is None:
            raise ValueError('Approved artwork state missing')
        artwork = (self.asset_root / item['file']).resolve()
        if not artwork.is_relative_to(self.asset_root) or artwork.suffix.lower() != '.gif':
            raise ValueError('Invalid approved artwork path')
        raw = artwork.read_bytes()
        if not 0 < len(raw) <= 4*1024*1024 or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Approved artwork hash or size differs')
        key = '/gif/'+item['sha256']+'.gif'
        with self.lock:
            self.asset_files[key] = raw
        return 'http://'+self.asset_host+':'+str(ASSET_PORT)+key, key, item

    def command(self, payload):
        with self.command_lock:
            return self._command(payload)

    def _command(self, payload):
        body = json.dumps({**payload, 'LocalToken': self.token}).encode()
        req = urllib.request.Request('http://' + self.ip + '/post', data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=6) as response:
            result = json.load(response)
        if result.get('error_code') not in (0, '0'):
            raise RuntimeError('Device rejected '+str(payload.get('Command',''))+' code='+str(result.get('error_code')))
        return result

    def frames(self, asset, state):
        cache_key = (asset, state)
        if cache_key in self.cache:
            return self.cache[cache_key]
        if self.asset_manifest is not None:
            screen = next((row['screen'] + 1 for row in self.config['screens'] if row['asset'] == asset), None)
            item = next((row for row in self.asset_manifest['files'] if (row.get('asset') == asset or (not row.get('asset') and row['screen'] == screen)) and row['state'] == state), None)
            if item is None:
                raise ValueError('Approved artwork state missing')
            artwork = (self.asset_root / item['file']).resolve()
            if not artwork.is_relative_to(self.asset_root) or artwork.suffix.lower() != '.gif':
                raise ValueError('Invalid approved artwork path')
            raw = artwork.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Approved artwork hash differs')
        elif asset == 'hand':
            text = (ROOT / 'supervisor-switch.js').read_text(encoding='utf-8')
            data = json.loads(re.search(r'const handAssets=Object.freeze\((\{.*?\})\);', text).group(1))
            url = data[state]['gif']
            if not re.fullmatch(r'/api/work/attachment\?id=[a-f0-9]{32}', url):
                raise ValueError('Unexpected original asset')
            raw = urllib.request.urlopen('http://127.0.0.1:8765' + url, timeout=8).read()
        else:
            if asset not in {'godo-planning', 'godo-design', 'godo-development', 'godo-research'}:
                raise ValueError('Unknown asset group')
            text = (ROOT / ('department-sprites-' + asset + '.js')).read_text(encoding='utf-8')
            data = json.loads(re.search(r'Object.freeze\((\{.*\})\);', text).group(1))
            url = data[state]['gif']
            prefix = 'data:image/gif;base64,'
            if not url.startswith(prefix):
                raise ValueError('Expected the existing GIF')
            raw = base64.b64decode(url[len(prefix):], validate=True)
        source = Image.open(io.BytesIO(raw))
        frames, ends, duration = [], [], 0
        for f in ImageSequence.Iterator(source):
            rgba = f.convert('RGBA')
            bg = Image.new('RGBA', rgba.size, 'black')
            bg.alpha_composite(rgba)
            frames.append(bg.convert('RGB').resize((128, 128), Image.Resampling.NEAREST))
            duration += max(10, f.info.get('duration', 100))
            ends.append(duration)
        # Uniform time sampling preserves the loop, including unequal source durations.
        count = min(38, max(len(frames), round(duration / 100)))
        speed = max(10, round(duration / count))
        result, selected = [], 0
        for index in range(count):
            stamp = index * duration / count
            while selected + 1 < len(ends) and stamp >= ends[selected]:
                selected += 1
            out = io.BytesIO()
            frames[selected].save(out, 'JPEG', quality=95, subsampling=0)
            result.append(base64.b64encode(out.getvalue()).decode('ascii'))
        self.cache[cache_key] = (result, speed)
        return result, speed

    def receive(self, value):
        if (not isinstance(value,dict) or not {'states'}<=set(value)<= {'states','source_id','activity_version'}
                or not isinstance(value['states'],list) or len(value['states'])>16):
            raise ValueError('Invalid state snapshot')
        activity_version=value.get('activity_version')
        if activity_version not in (None,2):
            raise ValueError('Invalid activity observation version')
        source=value.get('source_id')
        if source is None:
            with self.lock:
                self.ignored_snapshots+=1
            return {'accepted':False,'reason':'client-update-required'}
        if not isinstance(source,str) or not re.fullmatch('[0-9a-f]{32}',source):
            raise ValueError('Invalid observation source')
        known={x['thread'] for x in self.config['screens']}
        updates={}
        for item in value['states']:
            if not isinstance(item,dict) or set(item)!={'thread','state'}:
                raise ValueError('Invalid state item')
            if item['thread'] not in known or item['state'] not in {'idle','working','completed','unknown','attention'}:
                raise ValueError('Unregistered state')
            if item['thread'] in updates:
                raise ValueError('Duplicate state item')
            # Legacy completed means an unread final, not current activity.
            updates[item['thread']]='idle' if item['state']=='completed' and activity_version!=2 else item['state']
        if set(updates)!=known:
            raise ValueError('Incomplete state snapshot')
        with self.lock:
            now=time.monotonic()
            if (self.observation_source not in (None,source)
                    and now-self.last_heartbeat<45):
                self.ignored_snapshots+=1
                return {'accepted':False,'reason':'another-source-active'}
            self.observation_source=source
            self.activity_version=activity_version
            self.desired=updates
            self.last_heartbeat=now
            self.observed_at=time.time()
        return {'accepted':True}

    def snapshot(self):
        with self.lock:
            return {'revision': REVISION, 'pid': os.getpid(), 'device_ip': self.ip,
                    'asset_pack': self.asset_pack or 'original',
                    'connected_ui': time.monotonic() - self.last_heartbeat < 45,
                    'desired': dict(self.desired), 'screens': dict(self.actual),
                    'transmissions': self.transmissions, 'error': self.last_error,
                    'panels': dict(self.panel_actual), 'uploading': self.uploading,
                    'rotation_seconds': 3 if self.config.get('team_rotation_mode')=='continuous' else self.rotation_seconds,
                    'recent_presentations': list(self.recent_presentations),
                    'transport': 'continuous-team-gif' if self.config.get('team_rotation_mode')=='continuous' else 'gif-file-url',
                    'observed_at': self.observed_at, 'seconds_per_member': 3, 'asset_port': ASSET_PORT,
                    'observation_mode':'single-source-v1','observation_source':self.observation_source,
                    'activity_semantics':'current-work-v2','activity_version':self.activity_version,
                    'ignored_snapshots':self.ignored_snapshots,
                    'next_selection_in_seconds':round(max(0,self.continuous_ready_at-time.monotonic()),1),
                    'asset_downloads': sum(x['count'] for x in self.asset_served.values()),
                    'asset_request_count':self.asset_request_count,'asset_last_request':self.asset_last_request,
                    'served_gifs': [{'sha256': urlsplit(key).path[5:-4], **value} for key, value in self.asset_served.items()],
                    'model_calls': 0}

    def encode_hand_continuous(self, images):
        # Fixed shared palette and full-frame disposal avoid optimized GIF
        # subrectangle/palette transitions on the Times Gate decoder.
        frames=[]
        for encoded in images:
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as source:
                if source.size!=(128,128):
                    raise RuntimeError('Unexpected approved artwork dimensions')
                originals=[]
                ends=[]
                total=0
                for index in range(source.n_frames):
                    source.seek(index)
                    frame=Image.new('RGBA',(128,128),'black')
                    frame.alpha_composite(source.convert('RGBA'))
                    originals.append(frame.convert('RGB'))
                    total+=max(10,source.info.get('duration',100))
                    ends.append(total)
                for index in range(10):
                    stamp=(index*300)%total
                    selected=next(n for n,end in enumerate(ends) if end>stamp)
                    frames.append(originals[selected])
        sheet=Image.new('RGB',(128*len(images),128*10))
        for index,frame in enumerate(frames):
            sheet.paste(frame,((index%len(images))*128,(index//len(images))*128))
        palette=sheet.quantize(colors=128,method=Image.Quantize.MEDIANCUT)
        indexed=[frame.quantize(palette=palette,dither=Image.Dither.NONE) for frame in frames]
        output=io.BytesIO()
        indexed[0].save(output,format='GIF',save_all=True,append_images=indexed[1:],
            duration=300,loop=0,disposal=2,optimize=False,background=0,include_color_table=False)
        return output.getvalue()

    def continuous_asset(self, ordered, states):
        metadata=[]
        images=[]
        for row in ordered:
            observed=states[row['thread']]
            kind=observed if observed in {'working','completed'} else 'idle'
            _,key,item=self.asset_url(row['asset'],kind)
            with self.lock:
                images.append(base64.b64encode(self.asset_files[key]).decode('ascii'))
            metadata.append({'thread':row['thread'],'label':row['label'],
                             'state':kind,'observed_state':observed})
        cache_key=hashlib.sha256(json.dumps([(row['thread'],row['state']) for row in metadata]).encode()).hexdigest()
        if cache_key in self.continuous_cache:
            result=self.continuous_cache.pop(cache_key)
            self.continuous_cache[cache_key]=result
            return result[0],result[1],metadata,result[2]
        if ordered[0].get('group')=='hand':
            raw=self.encode_hand_continuous(images)
        else:
            node=shutil.which('node')
            modules=Path.home()/'.cache'/'codex-runtimes'/'codex-primary-runtime'/'dependencies'/'node'/'node_modules'
            if not node or not (modules/'sharp').is_dir():
                raise RuntimeError('The existing Node image runtime is unavailable')
            output=subprocess.run([node,str(ROOT/'divoom_continuous.mjs'),str(modules)],
                input=json.dumps({'members':images,'compact':ordered[0].get('group')=='hand'}).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if output.returncode:
                raise RuntimeError('Continuous artwork encoding failed')
            raw=output.stdout
        if not 0<len(raw)<=4*1024*1024:
            raise RuntimeError('Continuous artwork size is invalid')
        image=Image.open(io.BytesIO(raw))
        if image.size!=(128,128) or getattr(image,'n_frames',1)<2:
            raise RuntimeError('Continuous artwork geometry is invalid')
        frames=image.n_frames
        # Keep a .gif URL and valid content digest; a per-process GIF comment
        # makes the first device fetch observable even after helper restarts.
        if raw[-1:]!=b';':
            raise RuntimeError('Continuous GIF trailer is invalid')
        salt=self.asset_session.encode('ascii')
        raw=raw[:-1]+b'\x21\xfe'+bytes([len(salt)])+salt+b'\x00;'
        key='/gif/'+hashlib.sha256(raw).hexdigest()+'.gif'
        with self.lock:
            self.asset_files[key]=raw
        result=('http://'+self.asset_host+':'+str(ASSET_PORT)+key,key,frames)
        self.continuous_cache[cache_key]=result
        active={value.get('asset_key') for value in self.panel_actual.values()}
        while len(self.continuous_cache)>8:
            evict=next((k for k,v in self.continuous_cache.items() if v[1] not in active and k!=cache_key),None)
            if evict is None:
                break
            _,old_key,_=self.continuous_cache.pop(evict)
            with self.lock:
                self.asset_files.pop(old_key,None)
                self.asset_served.pop(old_key,None)
        return result[0],result[1],metadata,result[2]

    def run_continuous(self):
        while not self.stop.wait(.25):
            if time.monotonic()<self.next_attempt:
                continue
            with self.lock:
                states=dict(self.desired)
                fresh=time.monotonic()-self.last_heartbeat<45
            if not fresh:
                continue
            for panel,members in sorted(self.panel_groups.items(),key=lambda item:(item[0]!=self.continuous_next_panel,item[0])):
                if time.monotonic()<self.continuous_ready_at:
                    break
                if panel not in (0,1) or not all(states.get(x['thread']) in {'idle','working','completed','attention'} for x in members):
                    continue
                signature=tuple((x['thread'],states[x['thread']] if states[x['thread']] in {'working','completed'} else 'idle') for x in members)
                if self.continuous_signatures.get(panel)==signature:
                    continue
                previous=self.continuous_candidates.get(panel)
                now=time.monotonic()
                if previous is None or previous[0]!=signature:
                    self.continuous_candidates[panel]=(signature,now)
                    continue
                if now-previous[1]<2 or now-self.continuous_updates.get(panel,-100)<5:
                    continue
                start_index=0
                phase=self.continuous_phase.get(panel)
                if phase:
                    start_index=(phase[1]+int((now-phase[0])/3))%len(members)
                ordered=members[start_index:]+members[:start_index]
                try:
                    with self.lock:
                        self.uploading={'screen':panel+1,'mode':'continuous-team-gif'}
                    url,key,metadata,frames=self.continuous_asset(ordered,states)
                    started=time.monotonic()
                    self.command({'Command':'Device/PlayGif',
                                  'LcdArray':[int(i==panel) for i in range(5)],'FileName':[url]})
                    command_ms=round((time.monotonic()-started)*1000)
                    with self.asset_condition:
                        self.asset_condition.wait_for(lambda:key in self.asset_served or self.stop.is_set(),timeout=8)
                        if self.stop.is_set():
                            return
                        downloaded=dict(self.asset_served.get(key,{}))
                    if not downloaded:
                        raise RuntimeError('Continuous GIF fetch was not observed')
                    accepted={'screen':panel+1,'group':members[0].get('group',''),
                              'mode':'continuous-team-gif','asset_pack':self.asset_pack,
                              'encoding':'fixed-palette-full-frame' if members[0].get('group')=='hand' else 'sharp',
                              'asset_key':key,'members':metadata,'seconds_per_member':3,
                              'duration_ms':len(members)*3000,'frames':frames,
                              'accepted_at':time.time(),'command_ms':command_ms,'gif_bytes':downloaded['bytes']}
                    with self.lock:
                        self.panel_actual[str(panel+1)]=accepted
                        for row in metadata:
                            self.actual[row['thread']]={**row,'screen':panel+1,'mode':'included-in-continuous-gif','accepted_at':accepted['accepted_at']}
                        self.recent_presentations=(self.recent_presentations+[dict(accepted)])[-24:]
                        self.transmissions+=1
                        self.last_error=''
                    self.continuous_signatures[panel]=signature
                    self.continuous_ready_at=time.monotonic()+15
                    self.continuous_next_panel=(panel+1)%2
                    self.continuous_updates[panel]=time.monotonic()
                    self.continuous_phase[panel]=(time.monotonic(),start_index)
                    atomic_json(DATA/'runtime.json',self.snapshot())
                except Exception as exc:
                    with self.lock:
                        self.last_error=type(exc).__name__+': '+str(exc)[:180]
                    self.next_attempt=time.monotonic()+20
                    break
                finally:
                    with self.lock:
                        self.uploading=None

    def run(self):
        if self.config.get('team_rotation_mode')=='continuous':
            self.run_continuous()
            return
        while not self.stop.wait(.2):
            if time.monotonic() < self.next_attempt:
                continue
            for panel, members in self.panel_groups.items():
                with self.lock:
                    current = dict(self.desired)
                    fresh = time.monotonic() - self.last_heartbeat < 45
                    shown = self.panel_actual.get(str(panel + 1))
                if not fresh:
                    break
                cursor = self.rotation_cursor[panel]
                if shown and time.monotonic() >= self.rotation_due[panel]:
                    cursor = (cursor + 1) % len(members)
                row = members[cursor]
                # A missing/stale observation must not be invented as idle/completed.
                if current.get(row['thread']) in (None, 'unknown'):
                    found = next(((cursor + step) % len(members) for step in range(1, len(members) + 1)
                                  if current.get(members[(cursor + step) % len(members)]['thread']) not in (None, 'unknown')), None)
                    if found is None:
                        continue
                    cursor = found
                    row = members[cursor]
                state = current[row['thread']]
                kind = state if state in {'working', 'completed'} else 'idle'
                if shown and shown['thread'] == row['thread'] and shown['state'] == kind:
                    continue
                try:
                    url, asset_key, item = self.asset_url(row['asset'], kind)
                    with self.lock:
                        self.uploading = {'screen': panel+1, 'thread': row['thread']}
                    started = time.monotonic()
                    self.command({'Command': 'Device/PlayGif',
                                  'LcdArray': [int(i == panel) for i in range(5)],
                                  'FileName': [url]})
                    command_ms = round((time.monotonic()-started)*1000)
                    # A first use must actually fetch the complete GIF. A previously
                    # served immutable URL may be played from the device's cache.
                    with self.asset_condition:
                        self.asset_condition.wait_for(lambda: asset_key in self.asset_served
                                                      or self.stop.is_set(), timeout=5)
                        if self.stop.is_set():
                            return
                        downloaded = dict(self.asset_served.get(asset_key, {}))
                    if not downloaded:
                        raise RuntimeError('Device accepted selection but did not fetch the approved GIF')
                    accepted = {'screen': panel + 1, 'thread': row['thread'], 'label': row['label'],
                                'group': row.get('group', ''), 'state': kind, 'observed_state': state,
                                'asset_pack': self.asset_pack or 'original', 'accepted_at': time.time(),
                                'frames': item['frames'], 'transport': 'gif-file-url',
                                'command_ms': command_ms, 'gif_bytes': downloaded['bytes'],
                                'asset_download_count': downloaded['count']}
                    with self.lock:
                        self.actual[row['thread']] = dict(accepted)
                        self.panel_actual[str(panel + 1)] = dict(accepted)
                        self.recent_presentations = (self.recent_presentations + [dict(accepted)])[-24:]
                        self.rotation_cursor[panel] = cursor
                        self.rotation_due[panel] = time.monotonic() + self.rotation_seconds
                        self.transmissions += 1
                        self.last_error = ''
                    atomic_json(DATA / 'runtime.json', self.snapshot())
                except Exception as exc:
                    with self.lock:
                        self.last_error = type(exc).__name__+': '+str(exc)[:180]
                    self.next_attempt = time.monotonic() + 20
                    break
                finally:
                    with self.lock:
                        self.uploading = None



def serve():
    display = Display()

    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            return (self.headers.get('Host') in {'127.0.0.1:' + str(PORT), 'localhost:' + str(PORT)}
                    and self.headers.get('Origin') in ORIGINS)

        def reply(self, value, status=200):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            if self.headers.get('Origin') in ORIGINS:
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            if not self.allowed():
                return self.reply({'error': 'origin'}, 403)
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Office-Divoom')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()

        def do_GET(self):
            if not self.allowed():
                return self.reply({'error': 'origin'}, 403)
            url=urlsplit(self.path)
            query=parse_qs(url.query)
            try:
                if url.path == '/config':
                    return self.reply({'nonce': display.nonce, 'screens': display.config['screens']})
                if url.path == '/health':
                    result=display.snapshot()
                    with picker.lock:
                        result['native_recovery']=dict(picker.connection_recovery)
                    return self.reply(result)
                if url.path == '/picker/status':
                    return self.reply(picker.status())
                if url.path == '/picker/catalog':
                    return self.reply(picker.catalog(query.get('category',['Nature&Weather'])[0],
                                                     int(query.get('page',['1'])[0])))
                if url.path == '/picker/artwork':
                    asset=query.get('asset',[''])[0]
                    if not any(x['asset']==asset for x in picker.gifs()):
                        raise ValueError('등록된 디붐 GIF를 선택해 주세요.')
                    _,key,_=display.asset_url(asset,'idle')
                    raw=display.asset_files[key]
                    self.send_response(200)
                    self.send_header('Content-Type','image/gif')
                    self.send_header('Content-Length',str(len(raw)))
                    self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
                    self.send_header('Vary','Origin')
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.end_headers()
                    self.wfile.write(raw)
                    return
            except (ValueError, TypeError, KeyError) as exc:
                return self.reply({'error':str(exc)[:180]},400)
            except (OSError, RuntimeError):
                return self.reply({'error':'디붐 연결이나 공식 목록을 가져오지 못했어요. 잠시 후 다시 확인해 주세요.'},503)
            self.reply({'error': 'not_found'}, 404)

        def do_POST(self):
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Office-Divoom', ''), display.nonce):
                return self.reply({'error': 'origin_or_nonce'}, 403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:
                    raise ValueError('요청 크기를 확인해 주세요.')
                value=json.loads(self.rfile.read(length))
                if self.path=='/state':
                    return self.reply(display.receive(value))
                if self.path=='/picker/preview':
                    return self.reply(picker.choose(value))
                if self.path in {'/picker/commit','/picker/cancel'}:
                    if not isinstance(value,dict) or set(value)!={'preview_id'} or not isinstance(value['preview_id'],str) or not re.fullmatch('[0-9a-f]{24}',value['preview_id']):
                        raise ValueError('현재 미리보기를 다시 확인해 주세요.')
                    result=picker.commit(value['preview_id']) if self.path.endswith('commit') else picker.cancel(value['preview_id'])
                    return self.reply(result)
                return self.reply({'error':'not_found'},404)
            except (ValueError, TypeError, KeyError) as exc:
                self.reply({'error':str(exc)[:180]},400)
            except (OSError, RuntimeError):
                self.reply({'error':'기기와 통신하지 못했어요. 연결을 확인해 주세요.'},503)

        def log_message(self, *args):
            pass

    # Binding first enforces one controller and prevents duplicate worker startups.
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    try:
        display.start_asset_server()
    except Exception:
        server.server_close()
        raise
    from divoom_picker import Picker
    picker=Picker(display, CONFIG, atomic_json)
    threading.Thread(target=picker.watch, daemon=True, name='times-gate-preview-return').start()
    threading.Thread(target=display.run, daemon=True, name='times-gate-display').start()
    atomic_json(DATA / 'runtime.json', display.snapshot())
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        display.stop.set()
        if display.asset_server is not None:
            display.asset_server.shutdown()
            display.asset_server.server_close()
        server.server_close()


def start_background():
    """Start with Office when configured; an existing local sender is reused."""
    if not CONFIG.exists():
        return {'state': 'not_configured'}
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=.25):
            req = urllib.request.Request('http://127.0.0.1:' + str(PORT) + '/health',
                                         headers={'Origin': 'http://127.0.0.1:8765'})
            data = json.load(urllib.request.urlopen(req, timeout=2))
            if data.get('revision') == REVISION and data.get('device_ip') == '192.168.0.16':
                return {'state': 'already_running', 'pid': data.get('pid')}
            raise RuntimeError('Another application owns the display port')
    except OSError:
        pass
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'sender.stdout.log').open('ab') as output, (DATA / 'sender.stderr.log').open('ab') as error:
        process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve())], cwd=str(ROOT),
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=error,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'state': 'starting', 'pid': process.pid}


if __name__ == '__main__':
    serve()

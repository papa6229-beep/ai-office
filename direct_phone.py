"""Local-network, certificate-pinned Android notification receiver.

Only /v1/pair and /v1/sync exist on the LAN listener. Office APIs stay localhost.
Pairing secrets, TLS keys, device tokens and APKs never belong in source control.
"""
import base64
import datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
import uuid
import office_security
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'.provider_runtime'/'phone'
APK=ROOT/'.provider_runtime'/'android-build'/'aemeath-phone.apk'
PORT=8766
LOCK=threading.RLock()
_instance=None


def _digest(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _text(value,limit):
    if not isinstance(value,str) or len(value)>limit:raise ValueError('invalid input')
    return value


def _hex(value,size=64):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value):raise ValueError('invalid identity')
    return value


def _db(db):
    import office_notifications
    return office_notifications._db(db)


def addresses():
    values=set()
    for row in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
        value=ipaddress.ip_address(row[4][0])
        if value.is_private and not (value.is_loopback or value.is_link_local or value.is_unspecified):values.add(str(value))
    return sorted(values)


def _certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    PRIVATE.mkdir(parents=True,exist_ok=True)
    cert=PRIVATE/'receiver.pem';key=PRIVATE/'receiver-key.pem'
    if not cert.exists() or not key.exists():
        private=ec.generate_private_key(ec.SECP256R1())
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'AI OFFICE phone receiver')])
        now=datetime.datetime.now(datetime.timezone.utc)
        value=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5))
               .not_valid_after(now+datetime.timedelta(days=3650))
               .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).sign(private,hashes.SHA256()))
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        cert.write_bytes(value.public_bytes(serialization.Encoding.PEM))
    value=x509.load_pem_x509_certificate(cert.read_bytes())
    pin=value.fingerprint(hashes.SHA256()).hex()
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key)
    return context,pin


class Receiver:
    def __init__(self,db):
        self.db=db;self.last_seen=0;self.source_info={};self.last_error='';self.ticket=None
        with _db(db) as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS direct_phone_devices (
                id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT NOT NULL,seen REAL NOT NULL,last_seq INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS direct_phone_index (
                device TEXT NOT NULL,key TEXT NOT NULL,revision TEXT NOT NULL,office_id TEXT NOT NULL,active INTEGER NOT NULL,posted REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(device,key));
                CREATE TABLE IF NOT EXISTS direct_phone_commands (
                id TEXT PRIMARY KEY,device TEXT NOT NULL,key TEXT NOT NULL,revision TEXT NOT NULL,
                office_id TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);''')
            columns={row[1] for row in c.execute('PRAGMA table_info(direct_phone_commands)')}
            if 'kind' not in columns:c.execute("ALTER TABLE direct_phone_commands ADD COLUMN kind TEXT NOT NULL DEFAULT 'dismiss'")
            if 'payload' not in columns:c.execute("ALTER TABLE direct_phone_commands ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
            import notification_conversations
            notification_conversations.ensure(c)
        self.server=None
        self._connections=threading.BoundedSemaphore(16)
        self._pair_attempts={}
        try:
            context,self.pin=_certificate()
            owner=self
            class Server(ThreadingHTTPServer):
                daemon_threads=True
                def get_request(self):
                    conn,addr=self.socket.accept();conn.settimeout(8)
                    return conn,addr
                def verify_request(self,request,address):
                    return office_security.lan_peer(address[0])
                def process_request(self,request,address):
                    if not owner._connections.acquire(blocking=False):
                        self.shutdown_request(request);return
                    try:super().process_request(request,address)
                    except BaseException:
                        owner._connections.release();raise
                def process_request_thread(self,request,address):
                    try:
                        try:secured=context.wrap_socket(request,server_side=True)
                        except (OSError,ssl.SSLError):
                            self.shutdown_request(request);return
                        super().process_request_thread(secured,address)
                    finally:owner._connections.release()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    code=400;result={'error':'invalid request'}
                    try:
                        if self.headers.get_all('Origin'):raise ValueError()
                        size=office_security.json_length(self.headers,2*1024*1024)
                        if self.path not in {'/v1/pair','/v1/sync'}:raise ValueError()
                        if self.path=='/v1/pair' and not owner.pair_attempt(self.client_address[0]):
                            code=429;raise ValueError()
                        # Validate the bearer before reading untrusted sync bodies.
                        device=None
                        if self.path=='/v1/sync':
                            device=owner.authorize(self.headers.get('Authorization',''))
                            if not device:raise PermissionError()
                        raw=self.rfile.read(size)
                        if len(raw)!=size:raise ValueError()
                        value=json.loads(raw)
                        if not isinstance(value,dict):raise ValueError()
                        result=owner.pair(value) if self.path=='/v1/pair' else owner.sync(device,value)
                        code=200
                    except PermissionError:code=403;result={'error':'pairing or authentication required'}
                    except (ValueError,TypeError,KeyError,UnicodeError):pass
                    except Exception:code=503;result={'error':'receiver temporarily unavailable'}
                    raw=json.dumps(result,ensure_ascii=False).encode('utf-8')
                    try:
                        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
                        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
                        self.send_header('Connection','close');self.end_headers();self.wfile.write(raw)
                    except (OSError,ssl.SSLError):pass
                def do_GET(self):self.send_error(404)
            self.server=Server(('0.0.0.0',PORT),Handler)
            threading.Thread(target=self.server.serve_forever,name='office-direct-phone',daemon=True).start()
        except (OSError,ValueError):self.last_error='직접 수신기를 열지 못했어요. 포트와 암호화 설정을 확인해 주세요.'

    def pair_attempt(self,address):
        now=time.monotonic()
        with LOCK:
            self._pair_attempts={key:value for key,value in self._pair_attempts.items() if now-value[0]<60}
            start,count=self._pair_attempts.get(address,(now,0))
            if count>=10 or (address not in self._pair_attempts and len(self._pair_attempts)>=256):return False
            self._pair_attempts[address]=(start,count+1)
            return True

    def close(self):
        if self.server:self.server.shutdown();self.server.server_close()

    def status(self):
        with _db(self.db) as c:count=c.execute('SELECT count(*) FROM direct_phone_devices').fetchone()[0]
        connected=bool(self.last_seen and time.time()-self.last_seen<40)
        return {'state':'watching' if connected else 'unavailable' if self.last_error else 'waiting',
                'message':self.last_error or ('휴대폰 직접 연결됨' if connected else '휴대폰 직접 연결 대기 · 연결 버튼에서 QR을 확인해 주세요.' if not count else '휴대폰 응답 대기 · Wi-Fi와 휴대폰 앱을 확인해 주세요.'),
                'mode':'android_direct','uses_ai':False,'paired_devices':count,'last_seen':self.last_seen or None,
                'phone_connection':{'connected':connected,'complete':self.source_info.get('complete',False),
                                    'sources':self.source_info.get('sources',{})},'automatic_recovery':True}

    def recover(self):
        return {'requested':False,'message':'직접 연결은 휴대폰 앱이 자동 재시도해요. 처음 연결할 때는 휴대폰 연결 버튼에서 QR을 읽어 주세요.'}

    def pairing(self):
        if not self.server:raise ValueError(self.last_error)
        hosts=addresses()
        if not hosts:raise ValueError('PC의 사내망 주소를 찾지 못했어요.')
        secret=secrets.token_hex(32)
        with LOCK:self.ticket={'hash':_digest(secret),'expires':time.time()+300}
        uri='aemeath://pair?'+urlencode({'v':'1','host':hosts[0],'port':PORT,'pin':self.pin,'secret':secret})
        import qrcode
        image=qrcode.make(uri);stream=io.BytesIO();image.save(stream,format='PNG')
        return {'qr':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode('ascii'),
                'addresses':hosts,'port':PORT,'expires_in':300,'apk_available':APK.is_file(),
                'apk_url':'/api/work/phone-apk','paired_devices':self.status()['paired_devices']}

    def pair(self,value):
        identifier=_hex(value.get('device_id'),32);name=_text(value.get('name','Android'),100)
        secret=_text(value.get('secret',''),64)
        with LOCK:
            if not self.ticket or self.ticket['expires']<time.time() or not hmac.compare_digest(self.ticket['hash'],_digest(secret)):
                raise PermissionError()
            token=secrets.token_hex(32)
            with _db(self.db) as c:
                c.execute('INSERT OR REPLACE INTO direct_phone_devices VALUES (?,?,?,?,0)',(identifier,name,_digest(token),time.time()))
            self.ticket=None
        return {'token':identifier+'.'+token,'protocol':1}

    def authorize(self,header):
        if not header.startswith('Bearer ') or len(header)>112:return None
        parts=header[7:].split('.')
        if len(parts)!=2:return None
        try:identifier=_hex(parts[0],32);token=_hex(parts[1])
        except ValueError:return None
        with _db(self.db) as c:row=c.execute('SELECT token_hash FROM direct_phone_devices WHERE id=?',(identifier,)).fetchone()
        return {'id':identifier,'token_hash':_digest(token)} if row and hmac.compare_digest(row['token_hash'],_digest(token)) else None

    def revoke(self):
        with LOCK,_db(self.db) as c:
            c.execute('DELETE FROM direct_phone_devices');c.execute('DELETE FROM direct_phone_commands')
            c.execute("UPDATE office_notifications SET phone_sync='local_only' WHERE origin='android_direct' AND phone_sync='pending'")
            self.ticket=None;self.last_seen=0;self.source_info={}
        return {'message':'휴대폰 직접 연결을 해제했어요. 저장된 알림은 보존했어요.'}

    def sync(self,identity,value):
        from office_notifications import source_allowed
        import notification_conversations as conversations
        device=identity['id']
        if value.get('protocol') not in {1,2}:raise ValueError()
        sequence=value.get('sequence')
        if type(sequence) is not int or not 0<sequence<2**53:raise ValueError()
        events=value.get('notifications');active=value.get('active_keys');acks=value.get('acks',[])
        if not isinstance(events,list) or len(events)>150 or not isinstance(active,list) or len(active)>300 or not isinstance(acks,list) or len(acks)>100:raise ValueError()
        active={_hex(key) for key in active};now=time.time();sources={}
        normalized=[]
        for item in events:
            if not isinstance(item,dict):raise ValueError()
            key=_hex(item.get('key'));revision=_hex(item.get('revision'))
            source=_text(item.get('source',''),160);title=_text(item.get('title',''),400);body=_text(item.get('body',''),4000)
            posted=item.get('posted',0)
            if type(posted) not in (int,float) or not 0<posted<1e14 or not source or not (title or body):raise ValueError()
            if not source_allowed(source):continue
            metadata=None
            if value['protocol']==2 and item.get('package') and conversations.messenger(source):
                package=_text(item.get('package',''),200)
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{1,199}',package):raise ValueError()
                token=_hex(item.get('conversation'))
                kind=item.get('conversation_kind')
                if kind not in {'shortcut','person','notification'} or type(item.get('can_reply')) is not bool:raise ValueError()
                messages=item.get('messages',[])
                if not isinstance(messages,list) or len(messages)>30:raise ValueError()
                cleaned=[]
                for message in messages:
                    text=_text(message.get('text',''),4000);sender=_text(message.get('sender',''),400)
                    stamp=message.get('posted');direction=message.get('direction','in')
                    if type(stamp) not in (int,float) or not 0<stamp<1e14 or direction not in {'in','out'}:raise ValueError()
                    cleaned.append({'text':text,'sender':sender,'posted':min(now,max(now-7*86400,stamp/1000)),'direction':direction})
                metadata={'package':package,'conversation':token,'conversation_kind':kind,
                          'conversation_title':_text(item.get('conversation_title',''),400),
                          'can_reply':item['can_reply'],'reply_action':item.get('reply_action') is True,'messages':cleaned}
            normalized.append((key,revision,source,title,body,min(now,max(now-7*86400,posted/1000)),posted,metadata))
            sources[source]=sources.get(source,0)+1
        with LOCK,_db(self.db) as c:
            # Revocation cannot race a sync authorized before the revoke.
            authorized=c.execute('SELECT token_hash,last_seq FROM direct_phone_devices WHERE id=?',(device,)).fetchone()
            if not authorized or not hmac.compare_digest(authorized['token_hash'],identity['token_hash']):raise PermissionError()
            if sequence<=authorized['last_seq']:
                commands=self._commands(c,device,value['protocol'])
                return {'protocol':1,'commands':commands,'accepted':False,'stale':True}
            for key,revision,source,title,body,received,posted,metadata in normalized:
                old=c.execute('SELECT * FROM direct_phone_index WHERE device=? AND key=?',(device,key)).fetchone()
                if old and old['revision']==revision:
                    c.execute('UPDATE direct_phone_index SET active=1 WHERE device=? AND key=?',(device,key))
                    confirmed=c.execute('SELECT confirmed FROM office_notifications WHERE id=?',(old['office_id'],)).fetchone()
                    conversations.ingest(c,old['office_id'],device,key,revision,source,title,body,received,metadata,confirmed['confirmed'] if confirmed else None)
                    continue
                event='android:'+device+':'+key+':'+revision
                existing=c.execute('SELECT id FROM office_notifications WHERE event_key=?',(event,)).fetchone()
                # Buffered older revisions must never roll back the current card.
                if old and (existing or posted<old['posted']):continue
                if old:
                    c.execute("UPDATE office_notifications SET confirmed=COALESCE(confirmed,?),source_state='replaced',phone_sync='source_removed' WHERE id=?",(now,old['office_id']))
                identifier=existing['id'] if existing else uuid.uuid4().hex
                if not existing:
                    fingerprint=_digest(json.dumps([source,title,body],ensure_ascii=False))
                    c.execute("INSERT INTO office_notifications(id,source,title,body,received,confirmed,fingerprint,origin,event_key,phone_sync,source_state,ingested) VALUES (?,?,?,?,?,NULL,?,'android_direct',?,'local_only','present',?)",(identifier,source,title,body,received,fingerprint,event,now))
                c.execute('INSERT OR REPLACE INTO direct_phone_index VALUES (?,?,?,?,1,?)',(device,key,revision,identifier,posted))
                conversations.ingest(c,identifier,device,key,revision,source,title,body,received,metadata)
            if value.get('complete') is True:
                for row in c.execute('SELECT key,office_id FROM direct_phone_index WHERE device=? AND active=1',(device,)).fetchall():
                    if row['key'] not in active:
                        c.execute('UPDATE direct_phone_index SET active=0 WHERE device=? AND key=?',(device,row['key']))
                        c.execute("UPDATE office_notifications SET confirmed=COALESCE(confirmed,?),source_state='removed',phone_sync='source_removed' WHERE id=?",(now,row['office_id']))
            for ack in acks:
                if not isinstance(ack,dict):raise ValueError()
                command=_hex(ack.get('id'),32);state=ack.get('state')
                if state not in {'dismissed','not_present','unsupported','unavailable','accepted','stale','expired','failed','unknown','needs_unlock'}:raise ValueError()
                row=c.execute("SELECT office_id,kind FROM direct_phone_commands WHERE id=? AND device=?",(command,device)).fetchone()
                if row:
                    c.execute('UPDATE direct_phone_commands SET state=? WHERE id=?',(state,command))
                    if row['kind']=='reply':conversations.reply_result(c,command,state)
                    elif state in {'dismissed','not_present','unsupported','unavailable'}:
                        c.execute('UPDATE office_notifications SET phone_sync=? WHERE id=?',(state,row['office_id']))
            conversations.reconcile_removed(c)
            c.execute('UPDATE direct_phone_devices SET seen=?,last_seq=? WHERE id=?',(now,sequence,device))
            commands=self._commands(c,device,value['protocol'])
        self.last_seen=now;self.source_info={'complete':value.get('complete') is True,'sources':sources}
        return {'protocol':1,'commands':commands,'accepted':True}

    @staticmethod
    def _commands(c,device,protocol):
        import notification_conversations
        commands=[]
        for row in c.execute("SELECT * FROM direct_phone_commands WHERE device=? AND state='pending' ORDER BY created LIMIT 80",(device,)).fetchall():
            item={key:row[key] for key in ('id','key','revision')}
            if row['kind']=='reply':
                if protocol<2:continue
                payload=json.loads(row['payload'])
                if payload['expires']<time.time():
                    c.execute("UPDATE direct_phone_commands SET state='expired' WHERE id=?",(row['id'],))
                    notification_conversations.reply_result(c,row['id'],'expired');continue
                # This sync contains the phone's latest inventory. Never dispatch
                # an action for a notification removed or replaced since submit.
                active=c.execute('SELECT revision FROM direct_phone_index WHERE device=? AND key=? AND active=1',
                                 (device,row['key'])).fetchone()
                invalid='not_present' if not active else 'stale' if active['revision']!=row['revision'] else None
                if invalid:
                    c.execute('UPDATE direct_phone_commands SET state=? WHERE id=?',(invalid,row['id']))
                    notification_conversations.reply_result(c,row['id'],invalid);continue
                item.update(kind='reply',**{key:payload[key] for key in ('conversation','text','expires')})
            elif protocol>=2:item['kind']='dismiss'
            commands.append(item)
        return commands

    def acknowledge(self,items):
        results=[]
        with LOCK,_db(self.db) as c:
            for item in items:
                row=c.execute('SELECT * FROM direct_phone_index WHERE office_id=?',(item['id'],)).fetchone()
                if not row or not row['active']:state='not_present'
                elif not c.execute('SELECT 1 FROM direct_phone_devices WHERE id=?',(row['device'],)).fetchone():state='unavailable'
                else:
                    state='pending'
                    pending=c.execute("SELECT 1 FROM direct_phone_commands WHERE office_id=? AND state='pending' AND kind='dismiss'",(item['id'],)).fetchone()
                    if not pending:c.execute("INSERT INTO direct_phone_commands(id,device,key,revision,office_id,state,created) VALUES (?,?,?,?,?,'pending',?)",(uuid.uuid4().hex,row['device'],row['key'],row['revision'],item['id'],time.time()))
                results.append({'id':item['id'],'state':state})
        return results


def start(db):
    global _instance
    with LOCK:
        if _instance is None:_instance=Receiver(db)
        return _instance

"""Durable notification conversations. Explicit human replies only, no model calls."""
import hashlib
import json
import re
import time
import unicodedata

MESSENGERS = ('nateon', 'hanbiro', 'between', 'kakao')


def _hash(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def messenger(source):
    text = re.sub(r'\W+', '', unicodedata.normalize('NFKC', source).lower())
    for name, aliases in (('nateon', ('nateon','네이트온')), ('hanbiro', ('hanbiro','한비로')), ('between', ('between','비트윈')), ('kakao', ('kakaotalk','kakao','카카오톡','카카오'))):
        if any(alias in text for alias in aliases): return name
    return None


def ensure(c):
    c.execute('''CREATE TABLE IF NOT EXISTS notification_native_links (
        account TEXT NOT NULL,native_room TEXT NOT NULL,room_id TEXT NOT NULL,baseline REAL NOT NULL,
        synced REAL NOT NULL,PRIMARY KEY(account,native_room),UNIQUE(account,room_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_native_receipts (
        account TEXT NOT NULL,native_room TEXT NOT NULL,native_id TEXT NOT NULL,message_id TEXT NOT NULL,
        PRIMARY KEY(account,native_room,native_id))''')
    c.execute('CREATE TABLE IF NOT EXISTS notification_native_state (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS notification_pc_reads (room_id TEXT PRIMARY KEY,data TEXT NOT NULL)')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_pc_read_queue (
        room_id TEXT PRIMARY KEY,through REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
        next_try REAL NOT NULL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_rooms (
        id TEXT PRIMARY KEY,source TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,
        device TEXT NOT NULL,package TEXT NOT NULL,token TEXT NOT NULL,
        latest_id TEXT,latest_key TEXT,latest_revision TEXT,replyable INTEGER NOT NULL DEFAULT 0,
        observed REAL NOT NULL,updated REAL NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_room_receipts (
        office_id TEXT PRIMARY KEY,room_id TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_message_sources (
        office_id TEXT NOT NULL,message_id TEXT NOT NULL,PRIMARY KEY(office_id,message_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS notification_message_source_id ON notification_message_sources(message_id)')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_room_messages (
        id TEXT PRIMARY KEY,room_id TEXT NOT NULL,direction TEXT NOT NULL,sender TEXT NOT NULL,
        body TEXT NOT NULL,posted REAL NOT NULL,read_at REAL,state TEXT NOT NULL,command_id TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS room_message_time ON notification_room_messages(room_id,posted,id)')
    c.execute('''CREATE TABLE IF NOT EXISTS notification_reply_support (
        device TEXT NOT NULL,package TEXT NOT NULL,source TEXT NOT NULL,supported INTEGER NOT NULL,
        observed REAL NOT NULL,PRIMARY KEY(device,package))''')


def _message(c, room, message, read_at=None):
    direction = message.get('direction', 'in')
    native=c.execute('''SELECT DISTINCT m.id,m.sender,m.body FROM notification_room_messages m
        JOIN notification_native_receipts r ON r.message_id=m.id
        WHERE m.room_id=? AND m.direction=? AND m.state<>'superseded' AND abs(m.posted-?)<2''',
        (room,direction,message['posted'])).fetchall()
    title=c.execute('SELECT title FROM notification_rooms WHERE id=?',(room,)).fetchone()
    native=[r for r in native if r['body'].strip()==message['text'].strip() and (direction=='out' or r['sender']==message.get('sender') or (title and message.get('sender')==title['title']))]
    if len(native)==1:return native[0]['id']
    if direction=='out':
        echoes=c.execute("SELECT id FROM notification_room_messages WHERE room_id=? AND direction='out' AND command_id IS NOT NULL AND body=? AND abs(posted-?)<120",(room,message['text'],message['posted'])).fetchall()
        if len(echoes)==1:
            c.execute("UPDATE notification_room_messages SET state='observed' WHERE id=?",(echoes[0]['id'],));return echoes[0]['id']
    ident = _hash(room, direction, message['posted'], message.get('sender',''), message['text'])
    existing=c.execute('SELECT id FROM notification_room_messages WHERE room_id=? AND direction=? AND posted=? AND sender=? AND body=?',
                       (room,direction,message['posted'],message.get('sender',''),message['text'])).fetchone()
    if existing:return existing['id']
    c.execute('INSERT OR IGNORE INTO notification_room_messages VALUES (?,?,?,?,?,?,?,?,?)',
              (ident,room,direction,message.get('sender',''),message['text'],message['posted'],
               read_at if direction=='in' else time.time(),'received' if direction=='in' else 'observed',None))
    return ident


def ingest(c, office_id, device, key, revision, source, title, body, posted, metadata=None, read_at=None):
    if not messenger(source): return
    ensure(c)
    metadata = metadata or {}
    package = metadata.get('package','')
    token = metadata.get('conversation','')
    kind = metadata.get('conversation_kind','legacy')
    title = metadata.get('conversation_title') or title or '대화'
    if not token: token = _hash('legacy',key,title)
    room = _hash('legacy-display',messenger(source),title) if kind=='legacy' else _hash(device, package or messenger(source), token)
    now = time.time()
    old = c.execute('SELECT room_id FROM notification_room_receipts WHERE office_id=?',(office_id,)).fetchone()
    # A legacy projection may be replaced by authenticated richer metadata for the same receipt.
    if old and old['room_id'] != room:
        prior = c.execute('SELECT kind,title FROM notification_rooms WHERE id=?',(old['room_id'],)).fetchone()
        if prior and prior['kind']=='legacy' and prior['title']==title:
            c.execute('UPDATE notification_room_messages SET room_id=? WHERE room_id=?',(room,old['room_id']))
            c.execute('UPDATE notification_room_receipts SET room_id=? WHERE room_id=?',(room,old['room_id']))
            c.execute('DELETE FROM notification_rooms WHERE id=?',(old['room_id'],))
    c.execute('''INSERT INTO notification_rooms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET source=excluded.source,title=excluded.title,kind=excluded.kind,
        latest_id=CASE WHEN excluded.updated>=updated THEN excluded.latest_id ELSE latest_id END,
        latest_key=CASE WHEN excluded.updated>=updated THEN excluded.latest_key ELSE latest_key END,
        latest_revision=CASE WHEN excluded.updated>=updated THEN excluded.latest_revision ELSE latest_revision END,
        replyable=CASE WHEN excluded.updated>=updated THEN excluded.replyable ELSE replyable END,
        observed=excluded.observed,updated=max(updated,excluded.updated)''',
        (room,source,title,kind,device,package,token,office_id,key,revision,
         int(metadata.get('can_reply') is True),now,posted))
    c.execute('INSERT OR REPLACE INTO notification_room_receipts VALUES (?,?)',(office_id,room))
    messages = metadata.get('messages') or [{'text':body,'sender':title,'posted':posted,'direction':'in'}]
    for message in messages:
        if message['text']:
            ident=_message(c,room,message,read_at)
            c.execute('INSERT OR IGNORE INTO notification_message_sources VALUES (?,?)',(office_id,ident))
            if read_at is not None:
                c.execute("UPDATE notification_room_messages SET read_at=COALESCE(read_at,?) WHERE id=? AND direction='in'",(read_at,ident))
    if package:
        c.execute('INSERT OR REPLACE INTO notification_reply_support VALUES (?,?,?,?,?)',
                  (device,package,source,int(metadata.get('reply_action',metadata.get('can_reply')) is True),now))


def backfill(c):
    ensure(c)
    rows = c.execute('''SELECT n.* FROM office_notifications n LEFT JOIN notification_room_receipts r
        ON r.office_id=n.id LEFT JOIN notification_rooms room ON room.id=r.room_id
        WHERE (r.office_id IS NULL OR room.kind='legacy') AND n.superseded_by IS NULL
        ORDER BY n.rowid DESC LIMIT 4000''').fetchall()
    for row in reversed(rows):
        if not messenger(row['source']): continue
        parts = (row['event_key'] or '').split(':')
        device,key = (parts[1],parts[2]) if len(parts)==4 and parts[0]=='android' else ('legacy',messenger(row['source']))
        ingest(c,row['id'],device,key,parts[-1] if len(parts)==4 else '',row['source'],row['title'],row['body'],row['received'],read_at=row['confirmed'])


def room_state(c, row, now=None):
    now = now or time.time()
    for pending in c.execute("SELECT id,payload FROM direct_phone_commands WHERE kind='reply' AND state='pending'").fetchall():
        if json.loads(pending['payload']).get('expires',0)<now:
            c.execute("UPDATE direct_phone_commands SET state='expired' WHERE id=?",(pending['id'],))
            reply_result(c,pending['id'],'expired')
    active = c.execute('SELECT revision FROM direct_phone_index WHERE device=? AND key=? AND active=1',
                       (row['device'],row['latest_key'])).fetchone()
    device = c.execute('SELECT seen FROM direct_phone_devices WHERE id=?',(row['device'],)).fetchone()
    online = bool(device and now-device['seen']<30)
    pending = c.execute("SELECT 1 FROM direct_phone_commands cmd JOIN notification_room_receipts r ON r.office_id=cmd.office_id WHERE r.room_id=? AND cmd.kind='reply' AND cmd.state='pending'",(row['id'],)).fetchone()
    supported = bool(row['replyable'] and row['kind']!='legacy')
    can_reply = supported and online and bool(active and active['revision']==row['latest_revision']) and not pending
    reason = ('전송 결과를 기다리고 있어요.' if pending else
              '이전 알림은 이름 기준 묶음이에요. 연결 앱 업데이트 후 실제 대화방을 확인해요.' if row['kind']=='legacy' else
              '휴대폰 연결을 기다리고 있어요.' if not online else
              '현재 답장 가능한 원본 알림이 없어요. 새 메시지가 오면 다시 확인해요.' if not active else
              '이 알림에는 현재 안전하게 사용할 답장 연결이 없어요.' if not supported else
              '최신 알림을 동기화하고 있어요.' if not can_reply else '')
    phone_rows=c.execute('''SELECT i.active,n.phone_sync FROM notification_room_receipts r
        JOIN direct_phone_index i ON i.office_id=r.office_id
        JOIN office_notifications n ON n.id=r.office_id
        WHERE r.room_id=? ORDER BY COALESCE(n.ingested,n.received) DESC''',(row['id'],)).fetchall()
    active_count=sum(bool(r['active']) for r in phone_rows)
    dismiss_pending=bool(c.execute("SELECT 1 FROM direct_phone_commands cmd JOIN notification_room_receipts r ON r.office_id=cmd.office_id WHERE r.room_id=? AND cmd.kind='dismiss' AND cmd.state='pending'",(row['id'],)).fetchone())
    dismiss_state='pending' if dismiss_pending and active_count else phone_rows[0]['phone_sync'] if phone_rows else 'unknown'
    pc=c.execute('SELECT data FROM notification_pc_reads WHERE room_id=?',(row['id'],)).fetchone()
    linked=bool(c.execute('SELECT 1 FROM notification_native_links WHERE room_id=?',(row['id'],)).fetchone())
    pc=json.loads(pc['data']) if pc else {'state':'not_checked' if linked else 'unsupported','can_confirm':linked}
    pc['can_confirm']=linked
    if c.execute('SELECT 1 FROM notification_pc_read_queue WHERE room_id=?',(row['id'],)).fetchone():
        pc={**pc,'state':'pending'}
    latest=c.execute("SELECT max(posted) FROM notification_room_messages WHERE room_id=? AND state<>'superseded'",(row['id'],)).fetchone()[0]
    if pc.get('state')=='confirmed' and latest and latest>pc.get('through',0)+.002:pc={**pc,'state':'newer_messages'}
    return {'can_reply':can_reply,'reply_supported':supported,'reply_reason':reason,'phone_online':online,'pc_read':pc,
            'phone_notification':{'online':online,'active_count':active_count,'known':bool(phone_rows),
                'dismiss_state':dismiss_state,'can_dismiss':bool(online and active_count and not pending and not dismiss_pending and dismiss_state!='unsupported')}}


def reconcile_removed(c):
    """Settle only messages proven to belong to removed phone notifications.

    This is Office handling state, never a messenger read receipt. A message
    still represented by another active phone notification stays pending.
    """
    ensure(c)
    affected=c.execute('''SELECT m.room_id,max(m.posted) AS through FROM notification_room_messages m
        WHERE m.read_at IS NULL AND m.direction='in' AND EXISTS (
          SELECT 1 FROM notification_message_sources s JOIN office_notifications n ON n.id=s.office_id
          WHERE s.message_id=m.id AND n.source_state='removed' AND n.confirmed IS NOT NULL)
        AND NOT EXISTS (
          SELECT 1 FROM notification_message_sources s JOIN direct_phone_index i ON i.office_id=s.office_id
          WHERE s.message_id=m.id AND i.active=1) GROUP BY m.room_id''').fetchall()
    c.execute('''UPDATE notification_room_messages SET read_at=(
        SELECT max(n.confirmed) FROM notification_message_sources s
        JOIN office_notifications n ON n.id=s.office_id
        WHERE s.message_id=notification_room_messages.id AND n.source_state='removed')
        WHERE read_at IS NULL AND direction='in' AND EXISTS (
          SELECT 1 FROM notification_message_sources s JOIN office_notifications n ON n.id=s.office_id
          WHERE s.message_id=notification_room_messages.id AND n.source_state='removed' AND n.confirmed IS NOT NULL)
        AND NOT EXISTS (
          SELECT 1 FROM notification_message_sources s JOIN direct_phone_index i ON i.office_id=s.office_id
          WHERE s.message_id=notification_room_messages.id AND i.active=1)''')
    import messenger_read
    for row in affected:messenger_read.enqueue(c,row['room_id'],row['through'])


def summaries(c):
    backfill(c)
    rows = c.execute("""SELECT r.* FROM notification_rooms r ORDER BY max(r.updated,coalesce(
        (SELECT max(m.posted) FROM notification_room_messages m WHERE m.room_id=r.id AND m.state<>'superseded'),r.updated)) DESC LIMIT 80""").fetchall()
    items=[]
    for row in rows:
        last = c.execute("SELECT body,posted FROM notification_room_messages WHERE room_id=? AND state<>'superseded' ORDER BY posted DESC,rowid DESC LIMIT 1",(row['id'],)).fetchone()
        counts = c.execute("SELECT count(*) total,sum(CASE WHEN direction='in' AND read_at IS NULL THEN 1 ELSE 0 END) unread FROM notification_room_messages WHERE room_id=? AND state<>'superseded'",(row['id'],)).fetchone()
        items.append({'id':row['id'],'source':row['source'],'title':row['title'],'body':last['body'] if last else '',
                      'received':last['posted'] if last else row['updated'],'unread':counts['unread'] or 0,
                      'count':counts['total'],'identity':row['kind'],**room_state(c,row)})
        if messenger(row['source']) in {'hanbiro','nateon','between'}:
            incoming=c.execute('''SELECT rowid seq,id,sender,body,posted,read_at FROM notification_room_messages
                WHERE room_id=? AND direction='in' AND state<>'superseded'
                ORDER BY posted DESC,rowid DESC LIMIT 1''',(row['id'],)).fetchone()
            if incoming:items[-1]['incoming']={'id':incoming['id'],'seq':incoming['seq'],
                'sender':incoming['sender'],'preview':incoming['body'][:240],
                'received':incoming['posted'],'unread':incoming['read_at'] is None}
        local_pending=c.execute('''SELECT 1 FROM notification_room_receipts r JOIN office_notifications n ON n.id=r.office_id
            WHERE r.room_id=? AND n.confirmed IS NULL AND n.superseded_by IS NULL LIMIT 1''',(row['id'],)).fetchone()
        items[-1]['needs_confirmation']=bool(items[-1]['unread'] or local_pending)
        items[-1]['needs_sync']=bool(items[-1]['phone_notification']['active_count'] or
            items[-1]['pc_read'].get('can_confirm') and items[-1]['pc_read'].get('state') in {'pending','requested','unavailable','newer_messages'})
    return items


def thread(db, ident, before=None):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{64}',ident): raise ValueError('대화방을 다시 선택해 주세요.')
    import office_notifications
    with office_notifications._db(db) as c:
        backfill(c)
        row=c.execute('SELECT * FROM notification_rooms WHERE id=?',(ident,)).fetchone()
        if not row: raise ValueError('대화방을 찾지 못했어요.')
        boundary=None
        if before:
            boundary=c.execute('SELECT posted,rowid seq FROM notification_room_messages WHERE id=? AND room_id=?',(before,ident)).fetchone()
            if not boundary: raise ValueError('이전 메시지 위치를 확인해 주세요.')
        query="SELECT rowid seq,* FROM notification_room_messages WHERE room_id=? AND state<>'superseded'"
        args=[ident]
        if boundary:
            query+=' AND (posted<? OR (posted=? AND rowid<?))';args.extend([boundary['posted'],boundary['posted'],boundary['seq']])
        messages=c.execute(query+' ORDER BY posted DESC,rowid DESC LIMIT 101',args).fetchall()
        native=c.execute('SELECT synced FROM notification_native_links WHERE room_id=? ORDER BY synced DESC LIMIT 1',(ident,)).fetchone()
        return {'id':ident,'source':row['source'],'title':row['title'],**room_state(c,row),
                'refreshed_at':time.time(),'latest_message_at':messages[0]['posted'] if messages else row['updated'],
                'collection':{'pc_cache':bool(native),'pc_synced_at':native['synced'] if native else None},
                'messages':[dict(m) for m in reversed(messages[:100])],'has_older':len(messages)>100}


def read(db, ident, through=None):
    thread(db,ident)
    import office_notifications
    with office_notifications._db(db) as c:
        now=time.time()
        cutoff=now if through is None else through
        c.execute('UPDATE notification_room_messages SET read_at=? WHERE room_id=? AND read_at IS NULL AND posted<=?',(now,ident,cutoff))
        c.execute('''UPDATE office_notifications SET confirmed=COALESCE(confirmed,?) WHERE id IN
            (SELECT office_id FROM notification_room_receipts WHERE room_id=?) AND received<=?''',(now,ident,cutoff))
        import messenger_read
        messenger_read.enqueue(c,ident,cutoff)
    return thread(db,ident)


def read_all(c,through):
    """Settle captured local messages in one transaction; enqueue PC read sync.

    Phone/PC acknowledgment is separate from the user's Office handling state.
    Capture IDs before updates so later arrivals are never swept by the UPDATE.
    """
    backfill(c)
    messages=c.execute('''SELECT m.id,m.room_id,m.posted FROM notification_room_messages m
        WHERE m.read_at IS NULL AND m.direction='in' AND m.state<>'superseded' AND m.posted<=?
        AND NOT (EXISTS (SELECT 1 FROM notification_message_sources s WHERE s.message_id=m.id)
          AND NOT EXISTS (SELECT 1 FROM notification_message_sources s JOIN office_notifications n ON n.id=s.office_id
            WHERE s.message_id=m.id AND coalesce(n.ingested,n.received)<=?))''',(through,through)).fetchall()
    now=time.time();rooms={}
    for message in messages:rooms[message['room_id']]=max(rooms.get(message['room_id'],0),message['posted'])
    c.executemany('UPDATE notification_room_messages SET read_at=? WHERE id=? AND read_at IS NULL',[(now,m['id']) for m in messages])
    import messenger_read
    for ident,cutoff in rooms.items():messenger_read.enqueue(c,ident,cutoff)
    return {'messages':len(messages),'rooms':len(rooms)}


def reply(db, ident, request_id, text):
    if not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id): raise ValueError('전송 번호를 확인해 주세요.')
    if not isinstance(text,str) or not text.strip() or len(text)>2000: raise ValueError('메시지는 2000자 이내로 입력해 주세요.')
    thread(db,ident)
    import direct_phone
    with direct_phone.LOCK,direct_phone._db(db) as c:
        c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT kind,payload FROM direct_phone_commands WHERE id=?',(request_id,)).fetchone()
        if existing:
            prior=json.loads(existing['payload'])
            if existing['kind']!='reply' or prior.get('room_id')!=ident or prior.get('text')!=text: raise ValueError('동일 전송 번호의 내용이 달라요.')
        else:
            row=c.execute('SELECT * FROM notification_rooms WHERE id=?',(ident,)).fetchone()
            if not room_state(c,row)['can_reply']: raise ValueError(room_state(c,row)['reply_reason'])
            now=time.time()
            payload={'room_id':ident,'conversation':row['token'],'text':text,'expires':now+120}
            c.execute('INSERT INTO direct_phone_commands(id,device,key,revision,office_id,state,created,kind,payload) VALUES (?,?,?,?,?,?,?,?,?)',
                      (request_id,row['device'],row['latest_key'],row['latest_revision'],row['latest_id'],'pending',now,'reply',json.dumps(payload,ensure_ascii=False)))
            c.execute('INSERT INTO notification_room_messages VALUES (?,?,?,?,?,?,?,?,?)',
                      (_hash('outgoing',request_id),ident,'out','나',text,now,now,'pending',request_id))
    return thread(db,ident)


def reply_result(c, command, state):
    ensure(c)
    c.execute("UPDATE notification_room_messages SET state=CASE WHEN state='observed' THEN state ELSE ? END WHERE command_id=?",(state,command))


def dismiss(db, ident):
    thread(db,ident)
    import direct_phone,office_notifications
    with direct_phone._db(db) as c:
        pending=c.execute("SELECT 1 FROM direct_phone_commands cmd JOIN notification_room_receipts r ON r.office_id=cmd.office_id WHERE r.room_id=? AND cmd.kind='reply' AND cmd.state='pending'",(ident,)).fetchone()
        if pending:raise ValueError('답장 전송 결과를 확인한 뒤 휴대폰 알림을 정리해 주세요.')
        rows=c.execute('''SELECT r.office_id FROM notification_room_receipts r JOIN direct_phone_index i
            ON i.office_id=r.office_id WHERE r.room_id=? AND i.active=1''',(ident,)).fetchall()
    for row in rows:office_notifications.confirm(db,row['office_id'],mark_local=False)
    return thread(db,ident)


def confirm_both(db, ident):
    before=thread(db,ident)
    dismiss(db,ident)
    read(db,ident,through=before['latest_message_at'])
    import messenger_read
    messenger_read.confirm(db,ident,before['latest_message_at'])
    return thread(db,ident)


def support(c):
    ensure(c)
    return [dict(row) for row in c.execute('SELECT source,supported,observed FROM notification_reply_support ORDER BY observed DESC')]

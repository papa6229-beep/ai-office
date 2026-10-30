"""Read-only Hanbiro PC cache augmentation of proven existing phone rooms."""
import json
from pathlib import Path
import threading
import time

import notification_conversations as chat
import office_notifications

LOCK=threading.RLock()
_workers=set()


def _state(c,**value):
    old=c.execute("SELECT data FROM notification_native_state WHERE id='hanbiro'").fetchone()
    data=json.loads(old['data']) if old else {}
    data.update(value)
    c.execute("INSERT OR REPLACE INTO notification_native_state VALUES('hanbiro',?)",(json.dumps(data),))
    return data


def status(db):
    with office_notifications._db(db) as c:
        chat.ensure(c);row=c.execute("SELECT data FROM notification_native_state WHERE id='hanbiro'").fetchone()
        data=json.loads(row['data']) if row else {'state':'starting','rooms':0}
        return {k:v for k,v in data.items() if not k.startswith('_')}


def _proof(messages,phone):
    return {(m['id'],p['id']) for m in messages for p in phone
            if m['direction']==p['direction'] and abs(m['posted']-p['posted'])<2
            and m['text'].strip()==p['body'].strip()}


def _insert(c,account,native,room,message,baseline):
    seen=c.execute('SELECT 1 FROM notification_native_receipts WHERE account=? AND native_room=? AND native_id=?',
                   (account,native,message['id'])).fetchone()
    if seen:return False
    near=c.execute('''SELECT m.id,m.sender,m.command_id,m.body FROM notification_room_messages m
        WHERE m.room_id=? AND m.direction=? AND m.state<>'superseded' AND abs(m.posted-?)<2
        AND NOT EXISTS (SELECT 1 FROM notification_native_receipts r WHERE r.account=? AND r.native_room=? AND r.message_id=m.id)''',
        (room['id'],message['direction'],message['posted'],account,native)).fetchall()
    near=[r for r in near if r['body'].strip()==message['text'].strip() and (message['direction']=='out' or r['sender'] in {message['sender'],room['title']})]
    if not near and message['direction']=='out':
        near=c.execute('''SELECT id,sender,command_id FROM notification_room_messages
            WHERE room_id=? AND direction='out' AND body=? AND command_id IS NOT NULL AND abs(posted-?)<120''',
            (room['id'],message['text'],message['posted'])).fetchall()
    if len(near)==1:
        ident=near[0]['id']
        if message['direction']=='out':c.execute("UPDATE notification_room_messages SET state='observed' WHERE id=?",(ident,))
    else:
        ident=chat._hash('native-hanbiro',account,native,message['id'])
        read_at=time.time() if message['direction']=='out' or message['posted']<=baseline else None
        c.execute('INSERT OR IGNORE INTO notification_room_messages VALUES(?,?,?,?,?,?,?,?,?)',
                  (ident,room['id'],message['direction'],message['sender'],message['text'],message['posted'],read_at,
                   'observed' if message['direction']=='out' else 'received',None))
    c.execute('INSERT OR IGNORE INTO notification_native_receipts VALUES(?,?,?,?)',(account,native,message['id'],ident))
    return True


def _reconcile(c,account):
    """Keep the original phone record; retain redundant projections as hidden history."""
    rows=c.execute('''SELECT n.*,r.native_room,r.native_id FROM notification_native_receipts r
        JOIN notification_room_messages n ON n.id=r.message_id
        WHERE r.account=? AND n.state<>'superseded' AND n.command_id IS NULL''',(account,)).fetchall()
    for native in rows:
        if native['id']!=chat._hash('native-hanbiro',account,native['native_room'],native['native_id']):continue
        candidates=c.execute('''SELECT p.id,p.body,p.sender FROM notification_room_messages p
            WHERE p.room_id=? AND p.direction=? AND p.id<>? AND p.state<>'superseded' AND abs(p.posted-?)<2
            AND NOT EXISTS(SELECT 1 FROM notification_native_receipts r WHERE r.message_id=p.id)''',
            (native['room_id'],native['direction'],native['id'],native['posted'])).fetchall()
        candidates=[p for p in candidates if p['body'].strip()==native['body'].strip() and p['sender']==native['sender']]
        if len(candidates)!=1:continue
        c.execute('UPDATE notification_native_receipts SET message_id=? WHERE account=? AND native_room=? AND native_id=?',
                  (candidates[0]['id'],account,native['native_room'],native['native_id']))
        c.execute("UPDATE notification_room_messages SET state='superseded' WHERE id=?",(native['id'],))


def collect(db):
    import messenger_attachments
    with LOCK:
        with office_notifications._db(db) as c:
            chat.ensure(c)
            rooms=[dict(r) for r in c.execute("SELECT * FROM notification_rooms WHERE source='Hanbiro Talk' AND kind<>'legacy'")]
            links=[dict(r) for r in c.execute('SELECT * FROM notification_native_links')]
            prior=c.execute("SELECT data FROM notification_native_state WHERE id='hanbiro'").fetchone()
            prior=json.loads(prior['data']) if prior else {}
        if not rooms:return dict(state='waiting_for_rooms',rooms=0)
        snapshot=messenger_attachments._call(dict(action='chat_snapshot',titles=list({r['title'] for r in rooms}),
                                                room_ids=list({r['native_room'] for r in links})),timeout=30)
        account=snapshot['account'];now=time.time()
        if prior.get('_account') and prior['_account']!=account:
            with office_notifications._db(db) as c:_state(c,state='account_changed')
            return dict(state='account_changed',rooms=0)
        native={r['id']:r for r in snapshot['rooms']};inserted=0;bound=0
        with office_notifications._db(db) as c:
            chat.ensure(c)
            _reconcile(c,account)
            for room in rooms:
                binding=c.execute('SELECT * FROM notification_native_links WHERE account=? AND room_id=?',(account,room['id'])).fetchone()
                if not binding:
                    history=c.execute('SELECT id,body,direction,posted FROM notification_room_messages WHERE room_id=?',(room['id'],)).fetchall()
                    candidates=[]
                    for key,value in native.items():
                        proof=_proof(value['messages'],history)
                        # Different native IDs and different event timestamps/text,
                        # not repeated projections of the same notification.
                        unique={m['id'] for m in value['messages'] if any(p[0]==m['id'] for p in proof)}
                        distinct={(m['posted'],m['text']) for m in value['messages'] if m['id'] in unique}
                        if len(unique)>=2 and len(distinct)>=2:candidates.append(key)
                    if len(candidates)!=1:continue
                    key=candidates[0]
                    if c.execute('SELECT 1 FROM notification_native_links WHERE account=? AND native_room=?',(account,key)).fetchone():continue
                    c.execute('INSERT INTO notification_native_links VALUES(?,?,?,?,?)',(account,key,room['id'],now,now))
                    binding=c.execute('SELECT * FROM notification_native_links WHERE account=? AND native_room=?',(account,key)).fetchone()
                value=native.get(binding['native_room'])
                if not value:continue
                bound+=1
                for message in value['messages']:
                    inserted+=int(_insert(c,account,value['id'],room,message,binding['baseline']))
                # Keep Android's ordering clock independent of the PC message
                # clock: second-rounded phone timestamps must still refresh the
                # current reply key/revision after a millisecond PC observation.
                phone_time=c.execute('''SELECT max(n.received) FROM office_notifications n
                    JOIN notification_room_receipts r ON r.office_id=n.id WHERE r.room_id=?''',(room['id'],)).fetchone()[0]
                if phone_time is not None:c.execute('UPDATE notification_rooms SET updated=? WHERE id=?',(phone_time,room['id']))
                c.execute('UPDATE notification_native_links SET synced=? WHERE account=? AND native_room=?',(now,account,value['id']))
            value=_state(c,_account=account,state='cache_read',rooms=bound,last_sync=now,
                         linked_messages=inserted,scope=snapshot['scope'],mode='code',model_calls=0,foreground_ui=False)
        return {k:v for k,v in value.items() if not k.startswith('_')}


def start(db):
    key=str(Path(db).resolve())
    with LOCK:
        if key in _workers:return
        _workers.add(key)
    def run():
        while True:
            try:
                collect(db)
                import messenger_read
                messenger_read.drain(db)
            except Exception:
                try:
                    with office_notifications._db(db) as c:
                        chat.ensure(c);_state(c,state='retrying')
                except Exception:pass
            time.sleep(5)
    threading.Thread(target=run,daemon=True,name='hanbiro-pc-conversations').start()

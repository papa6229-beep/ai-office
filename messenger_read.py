"""Explicit, bounded PC read acknowledgement for an already proven room."""
import hashlib
import math
import re
import subprocess
import threading
import time

_sync_lock=threading.Lock()


def enqueue(c,ident,through):
    """Only a newly confirmed Office/phone range creates an intent, never a sweep."""
    import json
    if not isinstance(through,(int,float)) or not math.isfinite(through) or through<=0:return
    if not c.execute('SELECT 1 FROM notification_native_links WHERE room_id=?',(ident,)).fetchone():return
    previous=c.execute('SELECT data FROM notification_pc_reads WHERE room_id=?',(ident,)).fetchone()
    result=json.loads(previous[0]) if previous else {}
    if result.get('state')=='confirmed' and result.get('through',0)>=through:return
    c.execute('''INSERT INTO notification_pc_read_queue(room_id,through) VALUES (?,?)
        ON CONFLICT(room_id) DO UPDATE SET through=excluded.through,attempts=0,next_try=0
        WHERE excluded.through>notification_pc_read_queue.through''',(ident,through))


def drain(db):
    """Reuse the existing PC collector; one queued room per pass, bounded retries."""
    if not _sync_lock.acquire(blocking=False):return
    try:
        import office_notifications,notification_conversations as chat
        with office_notifications._db(db) as c:
            chat.ensure(c)
            job=c.execute('SELECT * FROM notification_pc_read_queue WHERE next_try<=? ORDER BY next_try,through LIMIT 1',
                          (time.time(),)).fetchone()
        if not job:return
        result=confirm(db,job['room_id'],job['through'])
        with office_notifications._db(db) as c:
            if result.get('state') in {'unavailable','requested'} and job['attempts']<2:
                c.execute('''UPDATE notification_pc_read_queue SET attempts=attempts+1,next_try=?
                    WHERE room_id=? AND through=?''',(time.time()+30,job['room_id'],job['through']))
            else:
                c.execute('DELETE FROM notification_pc_read_queue WHERE room_id=? AND through<=?',
                          (job['room_id'],job['through']))
    finally:_sync_lock.release()


def worker(account, room, through, boundary):
    import messenger_attachment_worker as intake
    import messenger_send
    if (not isinstance(account,str) or not re.fullmatch('[0-9a-f]{64}',account)
            or not isinstance(room,str) or not re.fullmatch('[0-9a-f]{16,64}',room)
            or not isinstance(through,(int,float)) or not math.isfinite(through) or through<=0
            or not isinstance(boundary,str) or not re.fullmatch('[0-9]{1,30}',boundary)):
        raise ValueError('읽음 처리할 대화와 확인 시점을 확인해 주세요.')
    if messenger_send._version()!='2.1.257':
        raise ValueError('현재 한비로 버전의 읽음 연결은 다시 확인해야 해요.')
    domain,_,data=intake._storage();own=data['_aemeath_current_user']
    if hashlib.sha256((domain+'|'+own).encode()).hexdigest()!=account:
        raise ValueError('연결된 한비로 계정이 바뀌었어요.')
    selected=data.get('recoil-atomAllRooms',{}).get('rooms',{}).get(room)
    if not selected or selected.get('leftRoom') or own not in selected.get('members',{}):
        raise ValueError('현재 참여 중인 원본 대화방을 확인해 주세요.')
    payload={'roomKey':room,'userKey':own}
    before=messenger_send._bridge('read_preflight',payload)
    if before['lastNumber']!=boundary:return {'state':'newer_messages'}
    if before['unread']==0 and before['read']==1:
        return {'state':'confirmed','before':0,'after':0,'mode':'code','model_calls':0,'foreground_ui':False}
    # Local outgoing timestamps precede the server timestamp. The exact native
    # receipt is the boundary; never widen it merely using a wall-clock margin.
    payload.update(lastNumber=before['lastNumber'],lastTime=before['lastTime'],
                   through=max(through,float(before['lastTime'])/1000))
    result=messenger_send._bridge('read_room',payload)
    return {**{k:result[k] for k in ('state','before','after') if k in result},
            'mode':'code','model_calls':0,'foreground_ui':False}


def confirm(db, ident, through):
    import office_notifications,notification_conversations as chat
    with office_notifications._db(db) as c:
        chat.ensure(c)
        link=c.execute('SELECT account,native_room FROM notification_native_links WHERE room_id=?',(ident,)).fetchone()
        room=c.execute('SELECT source FROM notification_rooms WHERE id=?',(ident,)).fetchone()
        boundary=c.execute('''SELECT r.native_id FROM notification_native_receipts r
            JOIN notification_room_messages m ON m.id=r.message_id
            WHERE m.room_id=? AND r.account=? AND r.native_room=?
              AND m.read_at IS NOT NULL AND m.posted<=? AND m.state<>'superseded'
            ORDER BY length(r.native_id) DESC,r.native_id DESC LIMIT 1''',
            (ident,link['account'] if link else '',link['native_room'] if link else '',through)).fetchone()
    if not room or chat.messenger(room['source'])!='hanbiro':return {'state':'unsupported','can_confirm':False}
    result={'state':'unlinked','can_confirm':False}
    if link and boundary:
        try:
            import messenger_attachments
            result=messenger_attachments._call({'action':'pc_read','account':link['account'],
                'room':link['native_room'],'through':through,'boundary':boundary['native_id']},timeout=15)
        except (ValueError,OSError,TimeoutError,subprocess.TimeoutExpired):result={'state':'unavailable'}
        result['can_confirm']=True
    result.update(through=through,checked_at=time.time())
    with office_notifications._db(db) as c:
        import json
        c.execute('INSERT OR REPLACE INTO notification_pc_reads VALUES (?,?)',(ident,json.dumps(result)))
        if result.get('state') not in {'unavailable','requested'}:
            c.execute('DELETE FROM notification_pc_read_queue WHERE room_id=? AND through<=?',(ident,through))
    return result

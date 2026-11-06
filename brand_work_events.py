"""Code-only, idempotent brand updates from verified file-operation receipts.

No chat/history inference, messenger replay, AI calls, or logistics guesses.
Private outbox survives network failures; only the board update is retried.
"""
from contextlib import closing
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import brand_status_registry as registry

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / 'local_backups/brand-status'
LOCK = threading.RLock()
WAKE = threading.Event()
_thread = None
PEOPLE = {'이석환', '안용훈', '조현진', '조혜연'}
EXCEL = {'.xlsx', '.xls', '.xlsm', '.csv'}


def config():
    path = PRIVATE / 'cloud.json'
    return registry.read(path) if path.exists() else {}


def _db():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(PRIVATE / 'cloud-outbox.sqlite3', timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT \'\')')
    c.commit()
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def cloud(path, payload=None):
    """Credentials are Windows-vault entries bound to this exact Site origin."""
    import credential_vault
    cfg = config()
    origin = credential_vault.origin(cfg['origin'])
    if not path.startswith('/api/') or path.startswith('//'):
        raise ValueError('현황판 경로를 확인해 주세요.')
    _, dispatch = credential_vault.load_for_origin(cfg['dispatch_credential'], origin)
    headers = {'OAI-Sites-Authorization': 'Bearer ' + dispatch, 'Accept': 'application/json'}
    if payload is not None:
        _, secret = credential_vault.load_for_origin(cfg['sync_credential'], origin)
        headers.update({'Content-Type': 'application/json', 'X-Office-Sync-Key': secret})
    request = urllib.request.Request(origin + path, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('현황판 응답이 너무 큽니다.')
        return json.loads(raw)


def _match(name, path, cfg, create):
    rows = (registry.registry(PRIVATE) or {}).get('batches', [])
    bindings = PRIVATE / 'cloud-bindings.json'
    if bindings.exists():
        known = {r['id'] for r in rows}
        rows += [r for r in registry.read(bindings).get('batches', []) if r['id'] not in known]
    norm = registry.normal(Path(name).stem)
    day = registry.file_date(name)
    named = [r for r in rows if any(a in norm for a in registry.aliases(r))]
    exact = [r for r in named if day and day in set(r.get('list_dates', []) + [r.get('list_work_date', '')])]
    explicit = [r for r in named if any(str(f.get('path', '')).casefold() == str(path).casefold() for f in r.get('files', []))]
    matches = exact or explicit
    if len(matches) == 1:
        r = matches[0]
        return r['id'], r['brand'], day or r.get('list_work_date', '')
    if matches or not create or not day:
        return None
    # A new dated price-list is a new batch, never the newest same-brand arrival.
    if named:
        brands = {r['brand'] for r in named}
        if len(brands) != 1:
            return None
        brand = brands.pop()
    else:
        stem = re.sub(r'^20\d{2}[._-]?\d{2}[._-]?\d{2}[_ .-]*', '', Path(name).stem)
        brand = re.split(r'(?:\d{2}년|\d+차|샘플|가격표|가격리스트|리스트|price)', stem, maxsplit=1, flags=re.I)[0].strip(' _-.')
        if not 1 <= len(brand) <= 40 or not re.search(r'가격|리스트|price', stem, re.I):
            return None
    ident = hashlib.sha256(('list:' + registry.normal(brand) + ':' + day).encode()).hexdigest()[:24]
    return ident, brand, day


def capture(key, kind, at, person='', data=None):
    """Called only after a successful receipt is saved. Never delays that action."""
    data = data or {}
    if kind == 'received' and data.get('saved_at'):
        at = data['saved_at']
    cfg = config()
    if not cfg or float(at) < cfg.get('cutoff', float('inf')):
        return
    name = data.get('name') or Path(data.get('current_path') or '').name
    path = data.get('current_path') or data.get('path') or ''
    if Path(name).suffix.casefold() not in EXCEL:
        return
    source = data.get('source')
    if kind == 'received' and source == 'nateon' and data.get('status') in {'pc_saved', 'confirmed'}:
        action = 'received'
    elif kind == 'move' and data.get('status') == 'completed' and path and Path(path).is_relative_to(Path(cfg['materials_root'])):
        action = 'received'
    elif kind == 'sent' and source == 'hanbiro' and person in PEOPLE and data.get('status') == 'confirmed':
        action = 'assigned'
    elif (kind == 'sent' and source == 'nateon' and data.get('status') == 'confirmed'
          and registry.normal(data.get('room')) in {registry.normal(r) for r in cfg.get('completion_rooms', [])}
          and '완료' in (name + path)):
        action = 'completed'
    else:
        return
    match = _match(name, path, cfg, create=action in {'received','assigned'})
    if not match:
        # Preserve a bounded unresolved receipt for review; never guess a batch.
        ident = hashlib.sha256(('brand:' + key).encode()).hexdigest()
        with LOCK, closing(_db()) as c:
            c.execute('INSERT OR IGNORE INTO events(id,payload,state,error) VALUES(?,?,?,?)',
                      (ident, json.dumps({'name': name, 'kind': action}, ensure_ascii=False), 'needs_batch', '입고 차수를 하나로 확인하지 못했습니다.'))
            c.commit()
        return
    ident, brand, day = match
    with LOCK:
        bindings = PRIVATE / 'cloud-bindings.json'
        saved = registry.read(bindings) if bindings.exists() else {'batches': []}
        if not any(r['id'] == ident for r in saved['batches']):
            saved['batches'].append({'id': ident, 'brand': brand, 'list_dates': [day]})
            registry.atomic(bindings, saved)
    payload = dict(id=hashlib.sha256(('brand:' + key).encode()).hexdigest(), batch_id=ident,
                   brand=brand, list_work_date=day, kind=action, at=float(at), person=person if action == 'assigned' else '')
    with LOCK, closing(_db()) as c:
        inserted = c.execute('INSERT OR IGNORE INTO events(id,payload,state) VALUES(?,?,?)',
                             (payload['id'], json.dumps(payload, ensure_ascii=False), 'pending')).rowcount
        c.commit()
    start()
    if inserted:
        WAKE.set()


def safe_capture(*args, **kwargs):
    try:
        capture(*args, **kwargs)
    except Exception as error:
        # Failed board recording never turns a successful file send into a retry.
        registry.atomic(PRIVATE / 'cloud-capture-error.json', {'at': time.time(), 'type': type(error).__name__})


def flush():
    """Retry board updates only. Source transfers are never invoked here."""
    with LOCK, closing(_db()) as c:
        rows = list(c.execute("SELECT * FROM events WHERE state='pending' AND next_at<=? ORDER BY rowid LIMIT 30", (time.time(),)))
    for row in rows:
        try:
            result = cloud('/api/office/event', json.loads(row['payload']))
            if not result.get('accepted'):
                raise ValueError('현황판 수락을 확인하지 못했습니다.')
            state, error, after = 'synced', '', 0
        except Exception as exc:
            state, error = 'pending', type(exc).__name__
            after = time.time() + min(300, 5 * 2 ** min(row['attempts'], 6))
        with LOCK, closing(_db()) as c:
            c.execute('UPDATE events SET state=?,attempts=attempts+1,next_at=?,error=? WHERE id=?', (state, after, error, row['id']))
            c.commit()


def reconcile_nateon():
    """A saved sent receipt is recoverable even if the process exited at callback."""
    cfg = config()
    if not cfg:
        return
    folder = ROOT / '.provider_runtime/nateon-avd/outgoing'
    for path in folder.glob('*.json'):
        if path.is_symlink() or path.stat().st_mtime < cfg['cutoff'] or path.stat().st_size > 65536:
            continue
        value = registry.read(path)
        if value.get('state') == 'sent' and value.get('confirmed') and value.get('path'):
            safe_capture('nateon-sent:' + path.stem, 'sent', value['confirmed'], data={
                'source': 'nateon', 'name': value.get('filename'), 'current_path': value['path'],
                'status': 'confirmed', 'room': value.get('room')})


def start():
    global _thread
    with LOCK:
        if not config() or _thread and _thread.is_alive():
            return
        def work():
            while True:
                try:
                    reconcile_nateon()
                    flush()
                    registry.atomic(PRIVATE / 'cloud-snapshot.json', cloud('/api/work/brand-status'))
                except Exception:
                    pass
                WAKE.wait(10)
                WAKE.clear()
        _thread = threading.Thread(target=work, name='brand-board-outbox', daemon=True)
        _thread.start()


def status():
    cfg = config()
    with closing(_db()) as c:
        counts = {r[0]: r[1] for r in c.execute('SELECT state,count(*) FROM events GROUP BY state')}
    return {'configured': bool(cfg), 'origin': cfg.get('origin'), 'counts': counts, 'mode': 'code', 'model_calls': 0}

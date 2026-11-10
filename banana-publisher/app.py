import base64
import errno
import hashlib
import hmac
import json
import os
import posixpath
import re
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import paramiko
from flask import Flask, abort, jsonify, redirect, render_template, request
from PIL import Image, UnidentifiedImageError


SKINS = {'web': ('웹', 20), 'mobile': ('모바일', 21)}
ADMIN = 'https://banana.bizhost.kr/adminx/newdesign.php'
PUBLIC_HOSTS = {'www.bananamall.co.kr', 'bananamall.co.kr', 'm.bananamall.co.kr'}
CDN_HOST = 'cdn-banana.bizhost.kr'
PAGE = re.compile(r'/etc/([A-Za-z0-9][A-Za-z0-9_-]{0,99})\.php')
NAME = re.compile(r'[\w][\w .-]{0,100}\.(?:jpe?g|png|gif|webp)', re.IGNORECASE)
FORMATS = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.gif': 'GIF', '.webp': 'WEBP'}
MAX_FILE = 20 * 1024 * 1024
MAX_FILES = 16
CHUNK = 128 * 1024


class PreparationError(ValueError):
    pass


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def missing_host_key(self, client, hostname, key):
        actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if not hmac.compare_digest(actual, self.fingerprint):
            raise PreparationError('확인된 SFTP 서버 지문과 현재 연결이 일치하지 않습니다.')


class EventImages(HTMLParser):
    def __init__(self, prefixes):
        super().__init__()
        self.prefixes = prefixes
        self.bases = set()
        self.images = {}

    def handle_starttag(self, tag, attributes):
        if tag not in {'img', 'source'}:
            return
        for key, value in attributes:
            if key not in {'src', 'data-src', 'srcset'} or not value:
                continue
            sources = [item.split()[0] for item in value.split(',') if item.split()] if key == 'srcset' else [value]
            for source in sources:
                try:
                    url = urlsplit(source.strip())
                except ValueError:
                    continue
                if url.netloc == CDN_HOST and url.scheme in {'', 'http', 'https'}:
                    for prefix, base in self.prefixes.items():
                        if not url.path.startswith(prefix):
                            continue
                        self.bases.add(base)
                        if tag == 'img' and key == 'src' and posixpath.basename(url.path):
                            preview = url._replace(scheme='https', fragment='').geturl()
                            self.images.setdefault(url.path, preview)


class EventRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if url.scheme != 'https' or url.netloc not in PUBLIC_HOSTS or url.path != urlsplit(request.full_url).path:
            raise PreparationError('공개 이벤트가 다른 주소로 이동했습니다.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


@dataclass
class Asset:
    name: str
    stream: object
    size: int
    sha256: str

    def remote_name(self, mode):
        if mode == 'new':
            return self.name
        stem, suffix = posixpath.splitext(self.name)
        return f'{stem}--{self.sha256[:16]}{suffix}'


def plan(raw_url):
    try:
        url = urlsplit(raw_url.strip())
    except ValueError:
        raise PreparationError('바나나몰 이벤트 페이지의 HTTPS 주소를 입력하세요.') from None
    match = PAGE.fullmatch(url.path)
    if url.scheme != 'https' or url.netloc not in PUBLIC_HOSTS or not match:
        raise PreparationError('바나나몰의 /etc/이벤트이름.php 주소를 입력하세요.')
    name = match[1]
    file = f'/etc/{name}.htm'
    return name, {skin: ADMIN + '?' + urlencode({'code': 'editor', 'skin_idx': index, 'fname': file, 'keys': 'htm,js,css'})
                  for skin, (_, index) in SKINS.items()}


def digest(stream, limit):
    checksum = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(CHUNK), b''):
        size += len(chunk)
        if size > limit:
            raise PreparationError('파일 크기가 허용 범위 또는 검증 대상의 크기를 넘었습니다.')
        checksum.update(chunk)
    return size, checksum.hexdigest()


def read_assets(files):
    if not 1 <= len(files) <= MAX_FILES:
        raise PreparationError(f'이미지를 1장부터 {MAX_FILES}장까지 첨부하세요.')
    assets = []
    seen = set()
    for file in files:
        name = file.filename or ''
        if not NAME.fullmatch(name) or len(name.encode('utf-8')) > 220 or name.casefold() in seen:
            raise PreparationError('중복되지 않는 JPG, PNG, GIF 또는 WebP 파일명을 사용하세요.')
        size, sha256 = digest(file.stream, MAX_FILE)
        file.stream.seek(0)
        try:
            with Image.open(file.stream) as image:
                if image.format != FORMATS[posixpath.splitext(name)[1].lower()] or image.width * image.height > 60_000_000:
                    raise PreparationError(f'{name}의 이미지 형식 또는 픽셀 크기를 확인하세요.')
                image.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
            raise PreparationError(f'{name}을 올바른 이미지 파일로 읽을 수 없습니다.') from None
        file.stream.seek(0)
        assets.append(Asset(name, file.stream, size, sha256))
        seen.add(name.casefold())
    return assets


def remote_stat(sftp, path):
    try:
        return sftp.lstat(path)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return None
        raise


def same_asset(sftp, path, asset):
    found = remote_stat(sftp, path)
    if found is None:
        return False
    if not stat.S_ISREG(found.st_mode) or found.st_size != asset.size:
        raise PreparationError(f'{posixpath.basename(path)} 이름으로 다른 항목이 있습니다.')
    with sftp.open(path, 'rb') as existing:
        if digest(existing, asset.size) != (asset.size, asset.sha256):
            raise PreparationError(f'{posixpath.basename(path)} 이름으로 내용이 다른 파일이 있습니다.')
    return True


def put_asset(sftp, path, asset):
    if same_asset(sftp, path, asset):
        return 'reused'
    temp = posixpath.join(posixpath.dirname(path), f'.publishing-{uuid.uuid4().hex}.part')
    try:
        asset.stream.seek(0)
        sftp.putfo(asset.stream, temp, file_size=asset.size, confirm=True)
        if not same_asset(sftp, temp, asset):
            raise PreparationError(f'{asset.name} 업로드 검증에 실패했습니다.')
        try:
            # Standard SFTP rename requires an absent destination.
            sftp.rename(temp, path)
        except OSError:
            if same_asset(sftp, path, asset):
                return 'reused'
            raise
        if not same_asset(sftp, path, asset):
            raise PreparationError(f'{asset.name} 최종 파일 검증에 실패했습니다.')
        return 'uploaded'
    finally:
        try:
            sftp.remove(temp)
        except OSError:
            pass


def settings():
    env = os.environ
    paths = {skin: env.get(f'BANANA_SFTP_{skin.upper()}_ETC', '').strip() for skin in SKINS}
    known_hosts = Path(env.get('BANANA_SFTP_KNOWN_HOSTS') or (Path.home() / '.ssh' / 'known_hosts'))
    host, user = env.get('BANANA_SFTP_HOST', '').strip(), env.get('BANANA_SFTP_USER', '').strip()
    password, key = env.get('BANANA_SFTP_PASSWORD'), env.get('BANANA_SFTP_KEY')
    port_value = env.get('BANANA_SFTP_PORT') or '20022'
    port = int(port_value) if re.fullmatch(r'[0-9]{1,5}', port_value) else 0
    fingerprint = env.get('BANANA_SFTP_HOST_SHA256', '').strip()
    pinned = bool(re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', fingerprint))
    if not fingerprint and host and known_hosts.is_file():
        try:
            host_key_name = host if port == 22 else f'[{host}]:{port}'
            pinned = paramiko.HostKeys(str(known_hosts)).lookup(host_key_name) is not None
        except (OSError, ValueError, paramiko.SSHException):
            pass
    roots_valid = all(path.startswith('/') and path.endswith('/etc') and posixpath.normpath(path) == path
                      for path in paths.values()) and len(set(paths.values())) == len(paths)
    ready = bool(host and user and (password or key) and pinned and roots_valid and 1 <= port <= 65535)
    return {'ready': ready, 'paths': paths, 'known_hosts': known_hosts, 'host': host, 'user': user,
            'password': password, 'key': key, 'port': port, 'fingerprint': fingerprint}


def replacement_sources(paths, name, *, allow_empty=False):
    prefixes = {f'/{posixpath.basename(posixpath.dirname(base))}/etc/{name}/': base for base in paths.values()}
    if len(prefixes) != len(paths):
        raise PreparationError('웹·모바일 스킨의 SFTP 설정을 구분해 주세요.')
    opener = build_opener(EventRedirects())
    sources = {
        'web': ('www.bananamall.co.kr', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'),
        'mobile': ('m.bananamall.co.kr', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1'),
    }
    verified, candidates = {}, {}
    for skin, (host, agent) in sources.items():
        label = SKINS[skin][0]
        page = Request(f'https://{host}/etc/{name}.php', headers={
            'User-Agent': agent, 'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
        try:
            with opener.open(page, timeout=8) as response:
                html = response.read(4 * 1024 * 1024 + 1)
                encoding = response.headers.get_content_charset() or 'utf-8'
        except OSError:
            raise PreparationError(f'{label} 공개 이벤트에서 기존 이미지 경로를 읽지 못했습니다.') from None
        if len(html) > 4 * 1024 * 1024:
            raise PreparationError(f'{label} 공개 이벤트가 확인할 수 있는 크기를 넘었습니다.')
        parser = EventImages(prefixes)
        try:
            parser.feed(html.decode(encoding, errors='replace'))
        except (LookupError, ValueError):
            raise PreparationError(f'{label} 공개 이벤트의 이미지 주소를 읽을 수 없습니다.') from None
        if allow_empty and not parser.bases:
            continue
        if len(parser.bases) != 1:
            raise PreparationError(f'{label} 공개 이벤트에서 기존 이미지 폴더 하나를 확인할 수 없습니다.')
        verified[skin] = parser.bases.pop()
        for path, preview in parser.images.items():
            item = candidates.setdefault(path, {'preview': preview, 'label': posixpath.basename(path), 'skins': []})
            item['skins'].append(skin)
    if not candidates and not allow_empty:
        raise PreparationError('공개 이벤트에서 교체할 수 있는 이미지 주소를 찾지 못했습니다.')
    if len(candidates) > 100:
        raise PreparationError('이벤트 이미지가 100장을 넘어 교체 대상을 준비할 수 없습니다.')
    return verified, list(candidates.values())


def prepare(config, name, mode, assets, results, paths):
    with paramiko.SSHClient() as ssh:
        if config['fingerprint']:
            ssh.set_missing_host_key_policy(PinnedHostKey(config['fingerprint']))
        else:
            ssh.load_host_keys(str(config['known_hosts']))
        ssh.connect(config['host'], port=config['port'], username=config['user'], password=config['password'],
                    key_filename=config['key'], allow_agent=False, look_for_keys=False,
                    timeout=12, banner_timeout=12, auth_timeout=12)
        with ssh.open_sftp() as sftp:
            sftp.get_channel().settimeout(25)
            folders = {}
            names = [asset.remote_name(mode) for asset in assets]
            for skin, (label, _) in SKINS.items():
                base = paths[skin]
                folder = posixpath.join(base, name)
                if folder in folders:
                    folders[folder][1].append(skin)
                    continue
                root = remote_stat(sftp, base)
                if root is None or not stat.S_ISDIR(root.st_mode):
                    raise PreparationError(f'{label} 스킨의 SFTP etc 폴더를 확인하세요.')
                found = remote_stat(sftp, folder)
                if found is None and mode == 'replace':
                    raise PreparationError(f'{label}에 기존 이벤트 폴더가 없습니다.')
                if found is not None and not stat.S_ISDIR(found.st_mode):
                    raise PreparationError(f'{label} 이벤트 경로가 일반 폴더가 아닙니다.')
                if found is not None:
                    for asset, filename in zip(assets, names):
                        same_asset(sftp, posixpath.join(folder, filename), asset)
                folders[folder] = (found is None, [skin])
            for folder, (missing, skins) in folders.items():
                for skin in skins:
                    results[skin].update(status='running', folder=folder, shared=len(skins) > 1)
                if missing:
                    sftp.mkdir(folder)
                for asset, filename in zip(assets, names):
                    status = put_asset(sftp, posixpath.join(folder, filename), asset)
                    for skin in skins:
                        results[skin]['files'].append({'name': filename, 'size': asset.size, 'status': status})
                for skin in skins:
                    results[skin]['status'] = 'ready'


def create_app():
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=100 * 1024 * 1024, MAX_FORM_PARTS=MAX_FILES + 4)
    public = urlsplit(os.environ.get('BANANA_PUBLISH_ORIGIN', 'http://127.0.0.1:8877'))
    local = public.hostname in {'127.0.0.1', 'localhost'}
    web_user, web_password = os.environ.get('BANANA_PUBLISH_WEB_USER'), os.environ.get('BANANA_PUBLISH_WEB_PASSWORD')
    if (not public.hostname or public.path not in ('', '/') or public.query or public.fragment or public.username
            or public.scheme not in {'https', 'http'} or (not local and (public.scheme != 'https' or not web_user or not web_password))
            or bool(web_user) != bool(web_password)):
        raise ValueError('팀 접속은 HTTPS 주소와 웹 로그인 설정이 필요합니다.')
    origin = f'{public.scheme}://{public.netloc}'
    lock = threading.Lock()
    selection_path = Path(os.environ.get('BANANA_PUBLISH_SELECTION_DB') or
                          Path.home() / '.banana-publisher' / 'replacements.sqlite3')
    office_python = Path(os.environ.get('BANANA_PUBLISH_OFFICE_PYTHON', ''))
    publish_enabled = local and office_python.is_file()

    def selection_db():
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(selection_path)
        db.execute('''CREATE TABLE IF NOT EXISTS replacements (
            id TEXT PRIMARY KEY, created INTEGER NOT NULL, plan TEXT NOT NULL,
            chosen INTEGER, outcome TEXT)''')
        return db

    @app.before_request
    def guard():
        if request.host != public.netloc:
            abort(403)
        if request.method == 'POST' and request.headers.get('Origin') != origin:
            abort(403)
        if web_user:
            supplied = request.authorization
            if (supplied is None or supplied.type != 'basic'
                    or not hmac.compare_digest((supplied.username or '').encode(), web_user.encode())
                    or not hmac.compare_digest((supplied.password or '').encode(), web_password.encode())):
                return '팀 웹 계정으로 로그인하세요.', 401, {'WWW-Authenticate': 'Basic realm="Banana publishing"'}

    @app.after_request
    def response_headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': f"default-src 'self'; img-src 'self' https://{CDN_HOST}; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get('/')
    def index():
        return render_template('index.html', sftp_ready=settings()['ready'], publish_enabled=publish_enabled, skins=SKINS)

    @app.get('/editor/<skin>')
    def open_editor(skin):
        if skin not in SKINS:
            abort(404)
        try:
            _, editors = plan(request.args.get('url', ''))
        except PreparationError as error:
            return str(error), 400
        return redirect(editors[skin])

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error='첨부 파일 합계 100MB와 최대 16장 범위를 확인하세요.'), 413

    @app.post('/api/prepare')
    def start_preparation():
        if not lock.acquire(blocking=False):
            return jsonify(error='다른 이벤트의 업로드가 진행 중입니다. 완료 후 다시 실행하세요.'), 409
        try:
            return process_preparation()
        finally:
            lock.release()

    @app.post('/api/replacements/<job_id>/publish')
    def publish_replacement(job_id):
        if not publish_enabled:
            return jsonify(error='이 서버에 비즈호스트 자동 게시 연결이 설정되지 않았습니다.'), 503
        if not lock.acquire(blocking=False):
            return jsonify(error='다른 이벤트 작업이 진행 중입니다. 완료 후 진행하세요.'), 409
        try:
            return process_publication(job_id)
        except sqlite3.Error:
            return jsonify(error='게시 기록을 확인할 수 없습니다. 현재 공개 페이지를 확인해 주세요.'), 500
        finally:
            lock.release()

    def process_publication(job_id):
        body = request.get_json(silent=True)
        choice = body.get('candidate') if isinstance(body, dict) else None
        if type(choice) is not int or choice < 0 or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            return jsonify(error='교체할 이미지를 선택하세요.'), 400
        with closing(selection_db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT plan, chosen, outcome, created FROM replacements WHERE id = ?', (job_id,)).fetchone()
            if row is None or (row[1] is None and row[3] < int(time.time()) - 86400):
                return jsonify(error='교체 준비 기록이 만료되었습니다. 수정 준비부터 다시 진행하세요.'), 404
            raw_plan, previous, recorded, _ = row
            job = json.loads(raw_plan)
            if choice >= len(job['candidates']):
                return jsonify(error='준비된 이미지 중에서 선택하세요.'), 400
            if previous is not None:
                if previous != choice:
                    return jsonify(error='이 준비 작업에는 이미 다른 이미지가 선택되어 있습니다.'), 409
                if recorded:
                    outcome = json.loads(recorded)
                    return jsonify(outcome), 200 if outcome['state'] == 'published' else 409
                return jsonify(error='이미 시작된 게시입니다. 현재 공개 페이지를 먼저 확인해 주세요.'), 409
            db.execute('UPDATE replacements SET chosen = ? WHERE id = ?', (choice, job_id))
        target = job['candidates'][choice]
        outcome = run_publication(job_id, job, target)
        with closing(selection_db()) as db, db:
            db.execute('UPDATE replacements SET outcome = ? WHERE id = ?',
                       (json.dumps(outcome, ensure_ascii=False), job_id))
        return jsonify(outcome), 200 if outcome['state'] == 'published' else 409

    def run_publication(job_id, job, target):
        if target.get('replacement'):
            try:
                with build_opener(EventRedirects()).open(target['replacement'], timeout=12) as image:
                    _, sha256 = digest(image, MAX_FILE)
                if sha256 != job['sha256']:
                    raise PreparationError('CDN에서 읽은 새 이미지가 첨부 원본과 다릅니다.')
            except (OSError, PreparationError):
                return dict(state='blocked', error='새 이미지를 공개 CDN에서 확인하지 못해 게시를 시작하지 않았습니다.')
        payload = dict(id=job_id, event=job['event'], editors=job['editors'], target=target)
        try:
            worker = Path(__file__).with_name('publish_edit.py').resolve()
            response = subprocess.run([str(office_python), '-X', 'utf8', str(worker)],
                                      input=json.dumps(payload), encoding='utf-8', capture_output=True, timeout=430,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
            outcome = json.loads(response.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            return dict(state='unconfirmed', error='비즈호스트 저장 결과를 받지 못했습니다. 공개 페이지를 먼저 확인해 주세요.')
        if outcome['state'] != 'saved':
            return outcome
        for attempt in range(4):
            if attempt:
                time.sleep(2)
            try:
                _, current = replacement_sources(settings()['paths'], job['event'], allow_empty=True)
            except PreparationError:
                continue
            old_path = urlsplit(target['preview']).path
            new_path = urlsplit(target.get('replacement', '')).path
            visible = {urlsplit(item['preview']).path: item['skins'] for item in current}
            if all(skin not in visible.get(old_path, ()) and (not new_path or skin in visible.get(new_path, ()))
                   for skin in target['skins']):
                return dict(state='published', skins=target['skins'])
        return dict(state='unconfirmed', error='비즈호스트 저장은 확인했습니다. 공개 페이지 반영을 아직 확인하지 못했습니다.',
                    skins=outcome['skins'])

    def process_preparation():
        results = {skin: {'status': 'waiting', 'files': []} for skin in SKINS}
        try:
            name, editors = plan(request.form.get('url', ''))
            mode = request.form.get('mode')
            if mode not in {'new', 'replace', 'delete'}:
                raise PreparationError('신규등록, 이미지 교체 또는 삭제를 선택하세요.')
            if mode != 'new' and not publish_enabled:
                return jsonify(error='이 서버에 비즈호스트 자동 게시 연결이 설정되지 않았습니다.'), 503
            config = settings()
            if not config['ready']:
                return jsonify(error='서버에 SFTP 로그인, 서버 키, 웹·모바일 etc 폴더 설정이 필요합니다.', editors=editors), 503
            assets = read_assets(request.files.getlist('images')) if mode != 'delete' else []
            if mode in {'replace', 'delete'}:
                if mode == 'replace' and len(assets) != 1:
                    raise PreparationError('한 장을 교체할 때는 새 이미지 한 장을 첨부하세요.')
                paths, candidates = replacement_sources(config['paths'], name)
            else:
                paths = dict.fromkeys(SKINS, config['paths']['web'])
            if assets:
                prepare(config, name, mode, assets, results, paths)
            payload = dict(state='ready', event=name, editors=editors, skins=results, mode=mode)
            if mode in {'replace', 'delete'}:
                if assets:
                    for item in candidates:
                        image_url = urlsplit(item['preview'])
                        root = image_url.path.split(f'/etc/{name}/', 1)[0]
                        item['replacement'] = f'https://{CDN_HOST}{root}/etc/{name}/{quote(assets[0].remote_name(mode))}'
                job_id, created = uuid.uuid4().hex, int(time.time())
                job = dict(event=name, candidates=candidates, editors=editors, sha256=assets[0].sha256 if assets else None)
                with closing(selection_db()) as db, db:
                    db.execute('DELETE FROM replacements WHERE chosen IS NULL AND created < ?', (created - 86400,))
                    db.execute('INSERT INTO replacements (id, created, plan) VALUES (?, ?, ?)',
                               (job_id, created, json.dumps(job, ensure_ascii=False)))
                payload.update(replacement_id=job_id, candidates=candidates)
            return jsonify(payload)
        except PreparationError as error:
            message, status = str(error), 400
        except paramiko.BadHostKeyException:
            message, status = '등록된 SFTP 서버 키가 현재 연결과 일치하지 않습니다.', 502
        except paramiko.AuthenticationException:
            message, status = 'SFTP 자동 로그인에 실패했습니다. 서버의 보관 계정을 확인하세요.', 502
        except sqlite3.Error:
            message, status = '이미지 선택 기록을 준비하지 못했습니다. 다시 실행하면 올린 파일을 먼저 대조합니다.', 500
        except (OSError, paramiko.SSHException):
            message, status = 'SFTP 연결 또는 파일 확인에 실패했습니다. 이미 확인된 파일은 아래에 표시됩니다.', 502
        for state in results.values():
            if state['status'] == 'running':
                state['status'] = 'failed'
        return jsonify(error=message, skins=results), status

    return app

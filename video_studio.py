"""Manual video workspace. No model calls; all media processing stays local."""
import contextlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'local_backups' / 'video-studio-data'
REVISION = 'manual-studio-2026-10-28.2'
LOCK = threading.RLock()
RUNNING = {}
MAX_UPLOAD = 100 * 1024 * 1024
QUOTA = 2 * 1024 * 1024 * 1024
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _safe(path):
    path = Path(path)
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('제작실 저장 경로를 확인해 주세요.')
    return path


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('제작실 항목을 다시 선택해 주세요.')
    return value


def _folder(ident):
    return _safe(DATA / _id(ident))


def _read(ident):
    path = _safe(_folder(ident) / 'project.json')
    if not path.is_file():
        raise ValueError('저장된 작업을 찾지 못했습니다.')
    return json.loads(path.read_text(encoding='utf-8'))


def _write(project):
    folder = _folder(project['id'])
    folder.mkdir(parents=True, exist_ok=True)
    target = _safe(folder / 'project.json')
    temporary = _safe(folder / (uuid.uuid4().hex + '.tmp'))
    temporary.write_text(json.dumps(project, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, target)


def _binary(name):
    bundled = ROOT / 'local_backups' / 'watch-runtime' / 'ffmpeg' / 'ffmpeg-9.0.2-essentials_build' / 'bin' / (name + '.exe')
    found = str(bundled) if bundled.is_file() else shutil.which(name)
    if not found:
        raise ValueError('이 PC에서 FFmpeg와 ffprobe를 찾지 못했습니다.')
    return found


def _probe(path):
    command = [_binary('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', 'mov,matroska,webm', '-show_streams', '-show_format', '-of', 'json', str(path)]
    result = subprocess.run(command, capture_output=True, timeout=20, creationflags=FLAGS)
    if result.returncode:
        raise ValueError('재생 가능한 MP4 또는 WebM 영상을 선택해 주세요.')
    info = json.loads(result.stdout)
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video:
        raise ValueError('영상 트랙이 없는 파일입니다.')
    duration = float(info.get('format', {}).get('duration', 0))
    width, height = int(video.get('width', 0)), int(video.get('height', 0))
    if not math.isfinite(duration) or not 0 < duration <= 60.2 or not 0 < width <= 4096 or not 0 < height <= 4096:
        raise ValueError('영상은 60초 이하, 가로·세로 각각 4096px 이하로 선택해 주세요.')
    return dict(duration=duration, width=width, height=height, codec=video.get('codec_name'),
                alpha=str(video.get('tags', {}).get('alpha_mode', '0')) == '1')


def snapshot(ident=None):
    with LOCK:
        _safe(DATA)
        projects = []
        if DATA.is_dir():
            for folder in DATA.iterdir():
                if re.fullmatch('[0-9a-f]{32}', folder.name):
                    item = _read(folder.name)
                    projects.append({k: item[k] for k in ('id', 'title', 'updated')})
        projects.sort(key=lambda x: x['updated'], reverse=True)
        selected = _read(ident) if ident else None
        if selected:
            for job in selected.get('jobs', []):
                if job['status'] == 'running' and job['id'] not in RUNNING:
                    job.update(status='interrupted', message='프로그램 종료로 중단됐습니다. 원본은 보관되어 있습니다.')
                elif job['status'] == 'running':
                    progress = _safe(_folder(ident) / (job['id'] + '.progress'))
                    if progress.exists():
                        raw = progress.read_text(encoding='utf-8', errors='replace')[-8000:]
                        times = re.findall(r'out_time_us=(\d+)', raw)
                        if times:
                            job['progress'] = min(99, int(int(times[-1]) / (job['duration'] * 10000)))
        try:
            _binary('ffmpeg'); _binary('ffprobe')
            available = True
        except ValueError:
            available = False
        return dict(revision=REVISION, projects=projects, project=selected, ffmpeg=available,
                    limits=dict(upload_mb=100, duration_seconds=60, output_edge=1920, output_options=True))


def _preferences(value):
    if not isinstance(value, dict):
        raise ValueError('영상 설정을 확인해 주세요.')
    resolution = value.get('generation_resolution', 720)
    seconds = value.get('generation_seconds', 8)
    edge = value.get('output_edge', 1920)
    trim = value.get('output_seconds', 0)
    if type(resolution) is not int or resolution not in {480, 720} or type(edge) is not int or edge not in {720, 1080, 1920}:
        raise ValueError('지원하는 해상도를 선택해 주세요.')
    if type(seconds) is not int or not 1 <= seconds <= 60 or type(trim) not in {int, float} or not math.isfinite(trim) or not 0 <= trim <= 60:
        raise ValueError('생성 희망 길이는 1~60초, 자를 길이는 0~60초로 선택해 주세요.')
    return dict(generation_resolution=resolution, generation_seconds=seconds, output_edge=edge, output_seconds=trim)


def save(payload):
    ident = _id(payload.get('id'))
    title, prompt = payload.get('title'), payload.get('prompt')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(prompt, str) or len(prompt) > 20000:
        raise ValueError('작업 이름은 120자, 프롬프트는 20,000자 이하로 입력해 주세요.')
    with LOCK:
        folder = _folder(ident)
        if (folder / 'project.json').exists():
            project = _read(ident)
        else:
            if DATA.exists() and len(list(DATA.glob('*/project.json'))) >= 100:
                raise ValueError('제작실에는 최대 100개 작업을 보관할 수 있습니다.')
            project = dict(id=ident, assets=[], jobs=[])
        preferences = _preferences(payload.get('preferences', project.get('preferences', {})))
        project.update(title=title.strip(), prompt=prompt, preferences=preferences, updated=time.time())
        _write(project)
    return snapshot(ident)


def upload(handler):
    ident = _id(handler.headers.get('X-Studio-Project'))
    role = handler.headers.get('X-Studio-Role')
    name = unquote(handler.headers.get('X-Studio-Filename', ''))
    name = name.replace('\\', '/').split('/')[-1]
    if role not in {'reference', 'source'} or not name or len(name) > 180 or any(ord(c) < 32 for c in name):
        raise ValueError('파일 이름과 용도를 확인해 주세요.')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm'} or (role == 'source' and suffix not in {'.mp4', '.webm'}):
        raise ValueError('참조는 PNG·JPG·WebP·MP4·WebM, 결과 영상은 MP4·WebM을 선택해 주세요.')
    lengths = handler.headers.get_all('Content-Length', [])
    if handler.headers.get_all('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        raise ValueError('파일 업로드 길이를 확인해 주세요.')
    size = int(lengths[0])
    if not 0 < size <= MAX_UPLOAD or (suffix not in {'.mp4', '.webm'} and size > 20 * 1024 * 1024):
        raise ValueError('영상은 100MB, 이미지는 20MB 이하로 선택해 주세요.')
    with LOCK:
        project = _read(ident)
        if len(project['assets']) >= 50:
            raise ValueError('한 작업에는 최대 50개 파일을 보관할 수 있습니다.')
        used = sum(_safe(p).stat().st_size for p in DATA.glob('*/*') if p.is_file())
        if used + size + (MAX_UPLOAD * 3 if RUNNING else 0) > QUOTA:
            raise ValueError('제작실 보관 공간 2GB를 초과합니다. 보관 파일을 정리해 주세요.')
        asset_id = uuid.uuid4().hex
        path = _safe(_folder(ident) / (asset_id + suffix))
        previous = handler.connection.gettimeout()
        try:
            handler.connection.settimeout(60)
            with path.open('xb') as stream:
                remaining = size
                while remaining:
                    chunk = handler.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('파일 업로드가 중단됐습니다.')
                    stream.write(chunk); remaining -= len(chunk)
            if suffix in {'.mp4', '.webm'}:
                metadata = _probe(path)
                mime, kind = ('video/mp4' if suffix == '.mp4' else 'video/webm'), 'video'
            else:
                from PIL import Image
                with Image.open(path) as image:
                    expected = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}[suffix]
                    if image.format != expected or max(image.size) > 4096 or min(image.size) < 1:
                        raise ValueError('참조 이미지는 4096px 이하 PNG·JPG·WebP로 선택해 주세요.')
                    image.verify()
                metadata = {}
                mime, kind = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[suffix], 'image'
            project['assets'].append(dict(id=asset_id, name=name, suffix=suffix, role=role, mime=mime, kind=kind, size=size, **metadata))
            project['updated'] = time.time(); _write(project)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            handler.connection.settimeout(previous)
    return snapshot(ident)


def _asset(project, ident):
    return next((a for a in project['assets'] if a['id'] == _id(ident)), None)


def convert(payload):
    ident, job_id = _id(payload.get('project')), _id(payload.get('request_id'))
    mode = payload.get('mode')
    color = {'green': '#00ff00', 'blue': '#0000ff', 'none': '#00ff00'}.get(mode, payload.get('color') if mode == 'custom' else None)
    similarity, blend = payload.get('similarity', .18), payload.get('blend', .06)
    preferences = _preferences(payload.get('preferences', {}))
    edge = preferences['output_edge']
    if not isinstance(color, str) or not re.fullmatch('#[0-9a-fA-F]{6}', color):
        raise ValueError('제거할 배경색을 선택해 주세요.')
    if any(type(x) not in {int, float} or not math.isfinite(x) for x in (similarity, blend)) or not .01 <= similarity <= .6 or not 0 <= blend <= .3:
        raise ValueError('색 허용 범위와 가장자리 값을 확인해 주세요.')
    with LOCK:
        project = _read(ident)
        if any(j['id'] == job_id for j in project['jobs']):
            return snapshot(ident)
        if RUNNING:
            raise ValueError('현재 영상을 처리 중입니다. 완료 후 다시 실행해 주세요.')
        if len(project['jobs']) >= 50 or len(project['assets']) >= 50:
            raise ValueError('이 작업의 보관 한도에 도달했습니다. 새 작업을 만들어 주세요.')
        used = sum(_safe(p).stat().st_size for p in DATA.glob('*/*') if p.is_file())
        if used + MAX_UPLOAD * 3 > QUOTA:
            raise ValueError('변환 결과를 보관할 공간이 부족합니다. 제작실 보관 공간은 2GB입니다.')
        source = _asset(project, payload.get('asset'))
        if not source or source['kind'] != 'video' or source['role'] == 'export':
            raise ValueError('가져온 원본 영상을 선택해 주세요.')
        original = _safe(_folder(ident) / (source['id'] + source['suffix']))
        meta = _probe(original)
        output_id = uuid.uuid4().hex
        output = _safe(_folder(ident) / (output_id + '.webm'))
        filters = [] if mode == 'none' else ['colorkey=0x' + color[1:] + ':' + str(similarity) + ':' + str(blend)]
        duration = min(meta['duration'], preferences['output_seconds'] or meta['duration'])
        filters += [f"scale=w='min({edge},iw)':h='min({edge},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2", 'format=yuva420p']
        command = [_binary('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
                   '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm', '-threads', '2']
        if meta['codec'] in {'vp8', 'vp9'} and meta['alpha']:
            command += ['-c:v', 'libvpx-vp9' if meta['codec'] == 'vp9' else 'libvpx']
        command += ['-i', str(original), '-map', '0:v:0', '-map', '0:a?', '-t', str(duration), '-vf', ','.join(filters),
                    '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', '-b:v', '0', '-crf', '25',
                    '-deadline', 'good', '-cpu-used', '4', '-threads', '2', '-filter_threads', '2', '-c:a', 'libopus', '-b:a', '128k',
                    '-progress', str(_safe(_folder(ident) / (job_id + '.progress'))), str(output)]
        import office_reload
        admission = office_reload.action()
        if not admission.__enter__():
            admission.__exit__(None, None, None)
            raise ValueError('오피스 업데이트가 끝난 뒤 다시 실행해 주세요.')
        job = dict(id=job_id, status='running', progress=0, duration=duration, source=source['id'],
                   mode=mode, color=color, similarity=similarity, blend=blend, output_edge=edge,
                   output_seconds=preferences['output_seconds'], created=time.time())
        try:
            project['jobs'].append(job); _write(project)
            control = dict(cancel=False, process=None)
            RUNNING[job_id] = control
            threading.Thread(target=_worker, args=(ident, job_id, command, output, output_id, admission, control), daemon=True).start()
        except Exception:
            RUNNING.pop(job_id, None); admission.__exit__(None, None, None)
            raise
    return snapshot(ident)


def _worker(ident, job_id, command, output, output_id, admission, control):
    status, message = 'failed', '변환에 실패했습니다. 원본과 배경색을 확인해 주세요.'
    error = _safe(_folder(ident) / (job_id + '.error'))
    try:
        with error.open('wb') as stderr:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, creationflags=FLAGS)
            control['process'] = process
            started = time.monotonic()
            while process.poll() is None:
                oversized = output.exists() and output.stat().st_size > MAX_UPLOAD * 3
                if control['cancel'] or time.monotonic() - started > 600 or oversized:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                    break
                time.sleep(.25)
            if control['cancel']:
                status, message = 'cancelled', '변환을 취소했습니다. 원본은 보관되어 있습니다.'
            elif process.returncode == 0 and output.is_file():
                metadata = _probe(output)
                if not metadata['alpha']:
                    raise ValueError('알파 채널 출력 확인에 실패했습니다.')
                status, message = 'done', '투명 WebM 저장 완료. 체크무늬·밝은 배경에서 가장자리를 확인해 주세요.'
        with LOCK:
            project = _read(ident)
            job = next(j for j in project['jobs'] if j['id'] == job_id)
            job.update(status=status, message=message, progress=100 if status == 'done' else 0)
            if status == 'done':
                job['output'] = output_id
                project['assets'].append(dict(id=output_id, name=project['title'][:90] + '_투명.webm', suffix='.webm',
                                              role='export', kind='video', mime='video/webm', size=output.stat().st_size, **metadata))
            project['updated'] = time.time(); _write(project)
    except Exception:
        with LOCK:
            project = _read(ident)
            next(j for j in project['jobs'] if j['id'] == job_id).update(status='failed', message='변환에 실패했습니다. 원본과 배경색을 확인해 주세요.')
            _write(project)
    finally:
        if status != 'done': output.unlink(missing_ok=True)
        error.unlink(missing_ok=True)
        _safe(_folder(ident) / (job_id + '.progress')).unlink(missing_ok=True)
        with LOCK: RUNNING.pop(job_id, None)
        admission.__exit__(None, None, None)


def cancel(payload):
    with LOCK:
        project = _read(_id(payload.get('project')))
        job_id = _id(payload.get('job'))
        if not any(j['id'] == job_id for j in project['jobs']):
            raise ValueError('선택한 변환 작업이 없습니다.')
        if job_id in RUNNING: RUNNING[job_id]['cancel'] = True
    return snapshot(project['id'])


def serve(handler, query):
    with LOCK:
        project = _read(query.get('project', [''])[0])
        asset = _asset(project, query.get('id', [''])[0])
        if not asset: raise ValueError('저장 파일이 없습니다.')
        path = _safe(_folder(project['id']) / (asset['id'] + asset['suffix']))
        stream = path.open('rb')
    with stream:
        size = os.fstat(stream.fileno()).st_size
        start, end, code = 0, size - 1, 200
        requested = handler.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                handler.send_response(416); handler.send_header('Content-Range', 'bytes */' + str(size)); handler.send_header('Content-Length', '0'); handler.end_headers(); return
            if not match[1]: start = max(0, size - int(match[2]))
            else: start, end = int(match[1]), min(int(match[2]) if match[2] else end, end)
            if start > end or start >= size:
                handler.send_response(416); handler.send_header('Content-Range', 'bytes */' + str(size)); handler.send_header('Content-Length', '0'); handler.end_headers(); return
            code = 206
        handler.send_response(code)
        handler.send_header('Content-Type', asset['mime']); handler.send_header('Accept-Ranges', 'bytes')
        handler.send_header('Content-Length', str(end - start + 1)); handler.send_header('Cache-Control', 'no-store')
        disposition = 'attachment' if query.get('download') == ['1'] else 'inline'
        handler.send_header('Content-Disposition', disposition + "; filename*=UTF-8''" + quote(asset['name']))
        if code == 206: handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        handler.end_headers(); stream.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk: break
            handler.wfile.write(chunk); remaining -= len(chunk)


def handle_get(handler, token):
    parsed = urlparse(handler.path); query = parse_qs(parsed.query)
    try:
        if parsed.path == '/api/video-studio/media': return serve(handler, query)
        if parsed.path != '/api/video-studio': return handler.reply({'error': 'not found'}, 404)
        return handler.reply(dict(snapshot(query.get('project', [None])[0]), action_token=token))
    except (BrokenPipeError, ConnectionResetError): pass
    except (ValueError, OSError): handler.reply({'error': '제작실 자료를 찾지 못했습니다.'}, 404)


def handle_post(handler, token):
    import office_security
    path = urlparse(handler.path).path
    try:
        if path == '/api/video-studio/upload':
            if len(handler.headers.get_all('X-Office-Token', [])) != 1 or not office_security.action_authorized({'token': handler.headers.get('X-Office-Token')}, token):
                handler.close_connection = True
                return handler.reply({'error': '오피스를 새로고침한 후 다시 시도해 주세요.'}, 403)
            return handler.reply(upload(handler))
        payload = handler.body()
        if not office_security.action_authorized(payload, token): return handler.reply({'error': '오피스를 새로고침해 주세요.'}, 403)
        action = {'/api/video-studio/save': save, '/api/video-studio/convert': convert, '/api/video-studio/cancel': cancel}.get(path)
        if not action: return handler.reply({'error': 'not found'}, 404)
        return handler.reply(action(payload))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        if path.endswith('/upload'): handler.close_connection = True
        handler.reply({'error': str(exc) if isinstance(exc, ValueError) else '파일을 처리하지 못했습니다. 형식과 크기를 확인해 주세요.'}, 400)

"""AI OFFICE localhost-only Git updater. No third-party packages required."""
import datetime, json, os, pathlib, shutil, socket, sqlite3, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parent
STATUS=ROOT/'update_status.json'
BACKUPS=ROOT/'local_backups'
PRIVATE=('office.db','settings.json','desktop_state.json')
TRACKED_PRIVATE=PRIVATE+('startup.log','office.db-wal','office.db-shm','office.db-journal','update_status.json','desktop_state.json.tmp')

def git(*args, timeout=45):
    return subprocess.run(['git','-C',str(ROOT),*args],text=True,capture_output=True,timeout=timeout,encoding='utf-8',errors='replace')

def write(phase,message,**extra):
    payload={'phase':phase,'message':message,'time':datetime.datetime.now().isoformat(timespec='seconds'),**extra}
    tmp=STATUS.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(STATUS)
    return payload

def check():
    if not (ROOT/'.git').exists():return {'phase':'error','message':'GitHub 설치 폴더가 아닙니다. .git 폴더가 없습니다.'}
    try:
        origin=git('remote','get-url','origin')
        if origin.returncode or 'papa6229-beep/ai-office' not in origin.stdout.lower().removesuffix('.git'):
            return {'phase':'error','message':'배포 저장소 주소가 일치하지 않습니다.'}
        branch=git('branch','--show-current')
        if branch.returncode or branch.stdout.strip()!='main':return {'phase':'error','message':'main 브랜치에서만 업데이트할 수 있습니다.'}
        tracked=git('ls-files',*TRACKED_PRIVATE)
        if tracked.returncode or tracked.stdout.strip():return {'phase':'error','message':'개인 DB·설정 등이 Git에 추적되고 있습니다. 먼저 저장소에서 추적을 해제해야 합니다.','tracked':tracked.stdout.strip()}
        dirty=git('status','--porcelain','--untracked-files=no')
        if dirty.returncode or dirty.stdout.strip():return {'phase':'error','message':'로컬 소스가 수정되어 있어 업데이트를 중단했습니다.','detail':dirty.stdout.strip()[:600]}
        return {'phase':'ready','message':'업데이트 준비 완료','branch':'main'}
    except (FileNotFoundError,subprocess.TimeoutExpired):return {'phase':'error','message':'Git 명령을 실행할 수 없습니다.'}

def fetch():
    pre=check()
    if pre['phase']!='ready':return pre
    result=git('fetch','--quiet','origin','main',timeout=90)
    if result.returncode:return {'phase':'error','message':'GitHub 접속에 실패했습니다. 인증 또는 네트워크를 확인하세요.','detail':result.stderr[-400:]}
    here=git('rev-parse','HEAD').stdout.strip()
    remote=git('rev-parse','origin/main').stdout.strip()
    ancestor=git('merge-base','--is-ancestor',here,remote)
    if ancestor.returncode:return {'phase':'error','message':'Git 이력이 갈라져 자동 업데이트할 수 없습니다.'}
    count=git('rev-list','--count',f'{here}..{remote}').stdout.strip()
    return {'phase':'available' if here!=remote else 'current','message':f'새 업데이트 {count}개' if here!=remote else '이미 최신 버전입니다.','current':here[:8],'latest':remote[:8], 'count':int(count or '0')}

def backup():
    BACKUPS.mkdir(exist_ok=True)
    folder=BACKUPS/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder.mkdir()
    if (ROOT/'office.db').exists():
        src=sqlite3.connect(str(ROOT/'office.db'))
        dest=sqlite3.connect(str(folder/'office.db'))
        try:src.backup(dest)
        finally:dest.close();src.close()
    for name in PRIVATE:
        if name!='office.db' and (ROOT/name).exists():shutil.copy2(ROOT/name,folder/name)
    return folder

def port_open():
    try:
        with socket.create_connection(('127.0.0.1',8765),timeout=0.3):return True
    except OSError:return False

def restore(folder):
    for name in PRIVATE:
        source=folder/name
        if source.exists():shutil.copy2(source,ROOT/name)

def restore_code(revision, files, expected_head=None):
    """Restore only files changed by this update; never reset the whole worktree."""
    paths=sorted({str(pathlib.PurePosixPath(p)) for p in files if p and not pathlib.PurePosixPath(p).is_absolute() and '..' not in pathlib.PurePosixPath(p).parts})
    if not paths:return
    dirty=git('status','--porcelain','--',*paths)
    if dirty.returncode or dirty.stdout.strip():raise RuntimeError('복구 대상 파일에 별도 변경이 있어 자동 복구를 중단했습니다.')
    if expected_head:
        current=git('rev-parse','HEAD')
        if current.returncode or current.stdout.strip()!=expected_head:raise RuntimeError('현재 커밋이 업데이트 결과와 달라 자동 복구를 중단했습니다.')
        moved=git('update-ref','HEAD',revision,expected_head)
        if moved.returncode:raise RuntimeError('업데이트 커밋 복구에 실패했습니다: '+moved.stderr[-300:])
    result=git('restore',f'--source={revision}','--staged','--worktree','--',*paths)
    if result.returncode:raise RuntimeError('업데이트 파일 복구 실패: '+result.stderr[-300:])

def launch():
    if os.name=='nt':
        subprocess.Popen(['cmd','/c','start.bat'],cwd=ROOT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NEW_CONSOLE)
    else:pass

def apply():
    write('checking','배포 상태를 확인 중입니다.')
    available=fetch()
    if available['phase']!='available':
        write(**available)
        launch()
        return
    before=git('rev-parse','HEAD').stdout.strip()
    folder=None
    try:
        write('backup','사용자 데이터를 백업하고 있습니다.')
        folder=backup()
        write('waiting','기존 서버가 종료되기를 기다리는 중입니다.',backup_dir=str(folder))
        for _ in range(100):
            if not port_open():break
            time.sleep(0.25)
        else:raise RuntimeError('기존 AI OFFICE 서버가 종료되지 않았습니다. 업데이트를 중단했습니다.')
        write('applying','GitHub 코드를 적용 중입니다.',backup_dir=str(folder))
        pull=git('merge','--ff-only','origin/main',timeout=60)
        if pull.returncode:raise RuntimeError('코드 갱신 실패: '+pull.stderr[-400:])
        merged_head=git('rev-parse','HEAD').stdout.strip()
        changed=git('diff','--name-only',before,'HEAD')
        if changed.returncode:raise RuntimeError('변경 파일 목록을 확인하지 못했습니다.')
        changed_files=changed.stdout.splitlines()
        files=['app.py','scanner.py','project_intelligence.py','workspace_insights.py','content_analysis.py','local_content_review.py','local_image_review.py','adobe_preview.py','desktop_workspace.py','desktop_preferences.py','desktop_collections.py','updater.py']
        compile_result=subprocess.run([sys.executable,'-c', 'import ast,pathlib,sys; [ast.parse(pathlib.Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:] if pathlib.Path(p).exists()]',*files],cwd=ROOT,capture_output=True,text=True,timeout=20)
        if compile_result.returncode:raise RuntimeError('새 코드 검사 실패: '+compile_result.stderr[-400:])
        write('done','업데이트가 완료되었습니다.',backup_dir=str(folder))
    except Exception as exc:
        write('rollback','오류가 발생하여 이전 코드를 복구합니다: '+str(exc),backup_dir=str(folder) if folder else '')
        if folder:
            rollback_errors=[]
            try:restore_code(before,changed_files if 'changed_files' in locals() else [],expected_head=merged_head if 'merged_head' in locals() else None)
            except Exception as rollback_error:rollback_errors.append(str(rollback_error))
            try:restore(folder)
            except Exception as rollback_error:rollback_errors.append(str(rollback_error))
            if rollback_errors:exc=RuntimeError(str(exc)+' / 복구 확인 필요: '+'; '.join(rollback_errors))
        write('error','업데이트 실패. 이전 버전을 유지했습니다: '+str(exc),backup_dir=str(folder) if folder else '')
    finally:
        launch()

if __name__=='__main__':
    if len(sys.argv)==2 and sys.argv[1]=='apply':apply()
    elif len(sys.argv)==2 and sys.argv[1]=='check':print(json.dumps(fetch(),ensure_ascii=False))

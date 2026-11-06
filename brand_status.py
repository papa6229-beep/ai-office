"""One-click brand work overview. Code reads; never sends or moves materials."""
import copy
import datetime as dt
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
import brand_status_registry

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'local_backups'/'brand-status'
REVISION='2026-11-06-arrival-batch-registry-v3'
LOCK=threading.Lock()
TZ=dt.timezone(dt.timedelta(hours=9))
_worker_lock=threading.Lock()
_worker=None


def start(db):
    """One local read-only collector, independent of whether the panel is open."""
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():return
        try:
            if not _read(PRIVATE/'source.json').get('auto_sync') or not (PRIVATE/'registry.json').exists():return
        except (OSError,ValueError):return
        def run():
            import time
            time.sleep(2)
            while True:
                try:refresh(db)
                except Exception:
                    # Cache remains intact; do not silently present a failed scan as fresh.
                    import brand_status_registry
                    brand_status_registry.atomic(PRIVATE/'sync-error.json',{'at':dt.datetime.now(TZ).isoformat(),
                        'message':'자동 대조 실패 · 마지막 성공 현황을 유지합니다.'})
                time.sleep(300)
        _worker=threading.Thread(target=run,name='brand-status-readonly',daemon=True)
        _worker.start()


def _read(path):
    if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('현황 자료를 확인해 주세요.')
    value=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(value,dict):raise ValueError('현황 자료 형식이 다릅니다.')
    return value


def _config():
    value=_read(PRIVATE/'source.json')
    folder=Path(value['materials_root']).resolve()
    source=Path(value['seed']).resolve()
    if not source.is_relative_to(folder) or source.suffix!='.json':raise ValueError('연결된 현황 자료를 확인해 주세요.')
    return folder,source


def snapshot(db):
    try:
        folder,source=_config()
        data=_read(PRIVATE/'current.json')if (PRIVATE/'current.json').exists()else _read(source)
    except (OSError,ValueError,KeyError):
        return {'configured':False,'brands':[],'events':[],'unresolved_materials':[],'warnings':['연결된 브랜드 현황 자료를 찾지 못했어요.'],'revision':REVISION}
    start(db)
    auto=dict(data.get('auto_sync')or{})
    auto.update(worker_running=bool(_worker and _worker.is_alive()),busy=LOCK.locked())
    error=PRIVATE/'sync-error.json'
    if error.exists():auto['error']=_read(error)
    return dict(data,configured=True,busy=LOCK.locked(),revision=REVISION,mode='code',model_calls=0,auto_sync=auto)


def refresh(db):
    if not LOCK.acquire(blocking=False):return snapshot(db)
    try:
        import design_team,brand_status_evidence,messenger_attachments,brand_status_registry
        folder,source=_config();data=copy.deepcopy(snapshot(db));warnings=[]
        canonical_previous=data.get('brands',[]) if data.get('batch_registry') else []
        if data.get('batch_registry'):data['brands']=data.get('other_batches',[])
        today=dt.datetime.now(TZ).date();previous=today-dt.timedelta(days=1)
        while previous.weekday()>=5:previous-=dt.timedelta(days=1)
        urls=design_team.sources();jobs=[]
        for member,name in design_team.MEMBERS:
            for day in (today,previous):
                token=urls[member].rsplit('/',1)[-1]
                jobs.append((member,name,day.isoformat(),design_team.ORIGIN+'/api/member/'+token+'/report?date='+day.isoformat()))
        def read_report(job):
            member,name,day,url=job
            try:
                payload=design_team.validate(design_team.fetch(url),member,name,'report',day)
                return name,day,payload.get('report')or{},True
            except (OSError,ValueError,KeyError,TypeError):return name,day,{},False
        with ThreadPoolExecutor(max_workers=4)as pool:reports=list(pool.map(read_report,jobs))
        latest={}
        for name,day,report,ok in reports:
            if not ok:warnings.append(name+' '+day+' 보고서 조회 실패')
            elif report.get('status')=='submitted' and day>=latest.get(name,('',''))[0]:latest[name]=(day,report)
        rows=data.setdefault('brands',[])
        for name,(day,report)in latest.items():
            for brand in report.get('brandUpdates',[]):
                matching=[r for r in rows if r.get('person')==name and r.get('brand')==brand.get('brand') and r.get('start_date')==brand.get('startDate')]
                if not matching:
                    if brand.get('status')=='완료' and brand.get('completedDate','')<'2026-10-22':continue
                    matching=[dict(brand=brand.get('brand',''),person=name,batch=brand.get('startDate')or day,start_date=brand.get('startDate'),folder='',delivery='보고서 담당 확인 · 자료 발신 미확인',image_source='미확인',note='보고서의 별도 배정분')];rows.extend(matching)
                for row in matching:
                    if day<(row.get('report_day')or''):continue
                    row.update(work_status=brand.get('status','미확인'),total=brand.get('totalCount'),remaining=brand.get('remainingCount'),completed=brand.get('completedDate'),report_day=day)
        try:
            evidence=messenger_attachments._call({'action':'brand_status_metadata'})
            baseline=brand_status_evidence._epoch(_read(source).get('as_of'))
            reconciled=copy.deepcopy(data)
            brand_status_evidence.reconcile(db,reconciled,folder,evidence,warnings,baseline)
            data=reconciled
        except (OSError,ValueError,KeyError,TypeError,ImportError):
            warnings.append('자료 발신·이동·완료파일 대조에 실패해 마지막 확인 상태를 유지했어요.')
        data['warnings']=warnings
        data['_canonical_previous']=canonical_previous
        data=brand_status_registry.project(db,data,folder,PRIVATE,_read(PRIVATE/'source.json'),latest)
        data.pop('_canonical_previous',None)
        data.setdefault('events_as_of',data.get('as_of'))
        data.update(as_of=dt.datetime.now(TZ).isoformat(timespec='seconds'),warnings=warnings,revision=REVISION,mode='code',model_calls=0,configured=True,busy=False)
        data.setdefault('coverage',{})['latest_submitted_reports']={name:day for name,(day,_)in latest.items()}
        data['coverage']['last_report_check']=data['as_of']
        PRIVATE.mkdir(parents=True,exist_ok=True)
        temporary=PRIVATE/'current.tmp';temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(PRIVATE/'current.json')
        (PRIVATE/'sync-error.json').unlink(missing_ok=True)
        return data
    finally:LOCK.release()

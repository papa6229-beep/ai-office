"""One-click brand work overview. Code reads; never sends or moves materials."""
import copy
import datetime as dt
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'local_backups'/'brand-status'
REVISION='2026-10-29-brand-status-v1'
LOCK=threading.Lock()
TZ=dt.timezone(dt.timedelta(hours=9))


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
    return dict(data,configured=True,busy=LOCK.locked(),revision=REVISION,mode='code',model_calls=0)


def refresh(db):
    if not LOCK.acquire(blocking=False):return snapshot(db)
    try:
        import design_team,messenger_attachment_worker as messenger
        folder,source=_config();data=copy.deepcopy(snapshot(db));warnings=[]
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
        pending=folder/'작업용파일_전달예정'
        known={str(Path(r['folder']).resolve())for r in rows if r.get('folder')}
        if pending.is_dir():
            for parent in pending.glob('*/*'):
                if not parent.is_dir() or parent.is_symlink() or str(parent.resolve())in known:continue
                files=[p for p in parent.iterdir()if p.is_file()and not p.name.startswith('~$')]
                if not files:continue
                date,_,brand=parent.name.partition(' ')
                rows.append(dict(brand=brand or parent.name,batch=date,person='미배정',folder=str(parent),delivery='전달대기(확인한 기록 기준)',work_status='대기',image_source='폴더 자료 확인',note='새로 발견한 전달예정 자료; 배정·이동은 하지 않음'))
        try:
            _,_,store=messenger._storage();own=store['_aemeath_current_user']
            people={n for _,n in design_team.MEMBERS};rooms={}
            for key,room in store.get('recoil-atomAllRooms',{}).get('rooms',{}).items():
                members=room.get('members',{});peers=[v for k,v in members.items()if k!=own]
                if not room.get('leftRoom')and own in members and len(members)==2 and len(peers)==1 and peers[0].get('name')in people:rooms[key]=peers[0]['name']
            receipts=[]
            for key,buckets in store.items():
                if not key.startswith('recoil-atomAllMessageList_'):continue
                for bucket in messenger._values(buckets):
                    if not isinstance(bucket,dict):continue
                    for room,name in rooms.items():
                        for message in messenger._message_rows(bucket.get(room,{})):
                            detail=message.get('messageDetail')or{}
                            if message.get('userKey')!=own or message.get('isDeleted')or message.get('isSending')or message.get('isPending')or not detail.get('httpServerFileName'):continue
                            receipts.append(dict(name=detail.get('fileName'),size=int(detail.get('fileSize')or 0),posted=str(message.get('createDate')or 0),room_title=name))
            for row in rows:
                parent=Path(row['folder'])if row.get('folder')else None
                if not parent or not parent.is_dir():continue
                prices=[p for p in parent.glob('*.xlsx')if not p.name.startswith('~$')]
                matches=[x for p in prices for x in receipts if x['name']==p.name and x['size']==p.stat().st_size and (row.get('person')in {'미배정','미확인'}or x['room_title']==row['person'])]
                if not matches:continue
                people={x['room_title']for x in matches}
                if len(people)!=1:
                    row['note']='동일 자료의 수신자가 여러 명으로 확인됨. 담당을 자동 변경하지 않음';continue
                match=max(matches,key=lambda x:int(x.get('posted')or'0'))
                stamp=dt.datetime.fromtimestamp(int(match['posted'])/1000,TZ).strftime('%m/%d %H:%M')
                row.update(person=match['room_title'],delivery='전달 확인 '+stamp)
        except (OSError,ValueError,KeyError,TypeError):warnings.append('한비로 연결·캐시를 읽지 못해 마지막 확인된 전달 상태를 유지했어요.')
        data.setdefault('events_as_of',data.get('as_of'))
        data.update(as_of=dt.datetime.now(TZ).isoformat(timespec='seconds'),warnings=warnings,revision=REVISION,mode='code',model_calls=0,configured=True,busy=False)
        data.setdefault('coverage',{})['latest_submitted_reports']={name:day for name,(day,_)in latest.items()}
        data['coverage']['last_report_check']=data['as_of']
        PRIVATE.mkdir(parents=True,exist_ok=True)
        temporary=PRIVATE/'current.tmp';temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');temporary.replace(PRIVATE/'current.json')
        return data
    finally:LOCK.release()

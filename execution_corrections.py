"""Correction links in the existing activation ledger, not another rule store."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parent
REVISION='2026-10-29-verified-correction-links-v2'


def bind(db,request_id,entries):
    """Trusted maintenance: attach bounded evidence to actual saved source IDs."""
    import office_current_task,source_activation
    office_current_task.require_current(request_id)
    if not isinstance(entries,list) or not 1<=len(entries)<=12:raise ValueError('교정 근거 목록을 확인하세요.')
    with closing(sqlite3.connect(str(db),timeout=10)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM '+source_activation.TABLE+' WHERE id=?',(request_id,)).fetchone()
        if not row:raise ValueError('실제 등록된 소스 변경이 먼저 필요합니다.')
        value=json.loads(row[0]);changes={r['path']:r for r in value['changes']}
        links={r['id']:r for r in value.get('corrections',[])}
        elsewhere={link['id'] for raw, in c.execute('SELECT data FROM '+source_activation.TABLE+' WHERE id<>?',(request_id,)) for link in json.loads(raw).get('corrections',[])}
        for entry in entries:
            if (set(entry)!={'id','scopes','files','evidence_ref','procedure_ref','verification_ref','validation_level'}
                    or not re.fullmatch('[a-z0-9-]{1,100}',str(entry['id']))
                    or entry['validation_level'] not in {'syntax_readonly','observed_business'}):
                raise ValueError('교정 식별자·확인 수준을 확인하세요.')
            if any(not isinstance(entry[k],list) or not 1<=len(entry[k])<=12 or any(not isinstance(s,str) or not s or len(s)>180 for s in entry[k]) for k in ('scopes','files')):
                raise ValueError('교정 적용 범위를 확인하세요.')
            if any(not isinstance(entry[k],str) or not 1<=len(entry[k])<=500 for k in ('evidence_ref','procedure_ref','verification_ref')):
                raise ValueError('교정 원문·검증 근거는 기존 기록의 위치로 연결하세요.')
            if any(name not in changes for name in entry['files']):raise ValueError('등록되지 않은 소스를 교정 완료로 연결하지 않았습니다.')
            linked={k:v for k,v in entry.items() if k!='files'}
            linked.update(origin_request_id=request_id,changes=[{k:changes[name][k] for k in ('path','change_id','sha256')} for name in entry['files']])
            if linked['id'] in elsewhere:raise ValueError('이미 등록된 교정 ID입니다. 기존 원장을 재사용하고 새 수정 버전은 별도 ID로 연결하세요.')
            if linked['id'] in links and links[linked['id']]!=linked:
                prior=links[linked['id']]
                if (value['state']!='awaiting_completion'
                        or {k:v for k,v in prior.items() if k!='changes'}!={k:v for k,v in linked.items() if k!='changes'}):
                    raise ValueError('공개된 교정이나 같은 ID의 다른 내용을 덮어쓰지 않았습니다.')
            links[linked['id']]=linked
        value['corrections']=list(links.values())
        c.execute('UPDATE '+source_activation.TABLE+' SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),request_id))
    return {'registered':len(links),'activation_state':value['state'],'revision':REVISION}


def references(db,scopes):
    """Content-free links for the next real execution's existing receipt."""
    import source_activation
    result={'revision':REVISION,'applied':[],'pending':[],'business_verification':'see_original_tool_result'}
    try:
        import office_reload
        with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(source_activation.TABLE,)).fetchone():return result
            rows=c.execute('SELECT data FROM '+source_activation.TABLE+' ORDER BY updated DESC LIMIT 200').fetchall()
        seen=set()
        for raw, in rows:
            activation=json.loads(raw)
            for link in activation.get('corrections',[]):
                if link['id'] in seen or not set(scopes).intersection(link['scopes']):continue
                seen.add(link['id'])
                effective=activation.get('current_changes') or activation['changes']
                proof=office_reload.activation_evidence(effective,activation.get('reload_id')) if activation.get('state')=='active' else None
                continuity=None
                if not proof:
                    continuity=source_activation.continuity_evidence(db,activation,link['id'])
                    if continuity:proof=continuity['proof'];effective=continuity['changes']
                if not proof:
                    result['pending'].append({'id':link['id'],'state':activation.get('state')});continue
                by_path={r['path']:r for r in effective}
                effective_link=[by_path[r['path']] for r in link['changes'] if r['path'] in by_path]
                if len(effective_link)!=len(link['changes']):
                    result['pending'].append({'id':link['id'],'state':'coverage_incomplete'});continue
                matching=True
                for change in effective_link:
                    path=(ROOT/change['path']).resolve()
                    if not path.is_relative_to(ROOT) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=change['sha256']:
                        matching=False;break
                if not matching:
                    result['pending'].append({'id':link['id'],'state':'source_changed'});continue
                result['applied'].append(dict({k:link[k] for k in ('id','origin_request_id','procedure_ref','verification_ref','validation_level')},
                    changes=effective_link,original_changes=link['changes'],
                    activation={'state':'active','request_id':continuity['request_id'] if continuity else activation.get('retained_by',activation['id']),
                                'reload_id':proof['ticket_id'],'loaded_pid':proof['pid'],'proof_revision':proof['proof_revision'],
                                'loaded_revisions':proof['runtime'],'basis':'verified_continuity' if continuity or activation.get('retained_by') else 'serving_process_proof'}))
        return result
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        # Metadata failure never replays or changes the outcome of a business action.
        return dict(result,applied=[],available=False,error_type=type(error).__name__)

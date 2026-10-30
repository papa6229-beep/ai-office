"""Explicit local UI selections for Times Gate panels 3–5 only."""
import json
import secrets
import threading
import time
import urllib.request

CATEGORIES = {'normal':'기본 화면', 'Nature&Weather':'시계·날씨', 'Retro':'레트로', 'Tools':'도구', 'saved':'원래 순환'}

class Picker:
    def __init__(self, display, config_path, save_json):
        self.display = display
        self.config_path = config_path
        self.save_json = save_json
        self.lock = threading.RLock()
        self.cache = {}
        self.pending = None
        self.error = ''
        self.next_connection_probe = 0
        self.restore_saved_screen = True
        self.connection_recovery = {'revision':'saved-screen-reconnect-v1','connected':None,
                                    'last_checked':None,'last_restored':None,'screen':3,'light_switch':None,
                                    'acknowledged':False,'error':''}
        self.pending_file = display.asset_root.parent.parent / 'selection-preview.json'

    def catalog(self, category='Nature&Weather', page=1):
        if category not in CATEGORIES or type(page) is not int or not 1 <= page <= 30:
            raise ValueError('목록 범위를 확인해 주세요.')
        if category=='saved':
            clock=self.display.config.get('original_gallery_clock_id')
            if type(clock) is not int or clock<=0:
                raise ValueError('원래 순환 채널 정보를 확인해야 해요.')
            return {'items':[{'kind':'native','clock_id':clock,'name':'원래 애니메이션 순환',
                              'category':'saved','page':1,'thumbnail':None}] if page==1 else [],
                    'category':'saved','page':page,'has_more':False}
        key = (category, page)
        with self.lock:
            cached = self.cache.get(key)
        if cached and time.monotonic()-cached[0] < 1800:
            return cached[1]
        req = urllib.request.Request('https://app.divoom-gz.com/Channel/GetDialList',
            data=json.dumps({'DialType':category,'DeviceType':'LCD','Page':page}).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read(256*1024+1)
        if len(raw)>256*1024:
            raise ValueError('목록이 너무 커서 가져오지 못했어요.')
        data = json.loads(raw)
        if not isinstance(data,dict) or data.get('ReturnCode') not in (0,'0') or not isinstance(data.get('DialList'),list):
            raise ValueError('디붐 공식 목록을 가져오지 못했어요.')
        items = []
        for row in data.get('DialList',[])[:30]:
            if not isinstance(row,dict):
                continue
            if type(row.get('ClockId')) is int and row['ClockId']>0 and isinstance(row.get('Name'),str):
                items.append({'kind':'native','clock_id':row['ClockId'],'name':row['Name'].strip()[:120],
                              'category':category,'page':page,'thumbnail':None})
        result = {'items':items,'category':category,'page':page,
                  'has_more':page*30<int(data.get('TotalNum',0)),
                  'preview_note':'기능성 화면은 공식 API에 이미지가 없어 디붐 기기에서 미리 봐요.'}
        with self.lock:
            self.cache[key] = (time.monotonic(),result)
        return result

    def gifs(self):
        return [{'kind':'gif','asset':x['asset'],'name':x['department'],
                 'preview':'/picker/artwork?asset='+x['asset']}
                for x in self.display.asset_manifest['files']
                if x.get('group')=='ambient' and x.get('state')=='idle'
                and x.get('source','').startswith('https://f.divoom-gz.com/')]

    def normalize(self, row, screen):
        if type(screen) is not int or screen not in (3,4,5):
            raise ValueError('3·4·5번 화면만 선택할 수 있어요.')
        if row.get('mode')=='gif' or row.get('kind')=='gif':
            item = next((x for x in self.gifs() if x['asset']==row.get('asset')),None)
            if not item:
                raise ValueError('현재 등록된 디붐 GIF를 선택해 주세요.')
            return {'screen':screen,'kind':'gif','asset':item['asset'],'name':item['name']}
        clock = row.get('clock_id')
        if type(clock) is not int or clock<=0:
            raise ValueError('현재 화면의 복원 정보를 찾지 못했어요.')
        return {'screen':screen,'kind':'native','clock_id':clock,'name':str(row.get('name',''))[:120]}

    def current(self, screen):
        config = json.loads(self.config_path.read_text(encoding='utf-8'))
        row = next((x for x in config.get('native_screens',[]) if x.get('screen')==screen-1),None)
        if row is None:
            raise ValueError('원래 화면 정보를 먼저 확인해야 해요.')
        return self.normalize(row,screen)

    def render(self, choice):
        screen = choice['screen']
        if screen not in (3,4,5):
            raise ValueError('팀 상태 화면은 변경할 수 없어요.')
        if choice['kind']=='gif':
            url,_,_ = self.display.asset_url(choice['asset'],'idle')
            self.display.command({'Command':'Device/PlayGif',
                'LcdArray':[int(i==screen-1) for i in range(5)],'FileName':[url]})
        else:
            group = self.display.config.get('native_group_id')
            if type(group) is not int or group<=0:
                raise ValueError('기기의 개별 화면 그룹 정보가 필요해요.')
            self.display.command({'Command':'Channel/SetClockSelectId',
                'ClockId':choice['clock_id'],'LcdIndex':screen-1,'LcdIndependence':group})

    def status(self):
        with self.lock:
            pending = None
            if self.pending:
                pending = {k:self.pending[k] for k in ('id','choice')}
                pending['seconds_left'] = max(0,round(self.pending['until']-time.monotonic()))
            return {'categories':[{'id':k,'name':v} for k,v in CATEGORIES.items()],
                    'screens':[self.current(i) for i in (3,4,5)],'gifs':self.gifs(),
                    'preview':pending,'error':self.error,'preview_seconds':60,
                    'connection_recovery':dict(self.connection_recovery)}

    def choose(self, value):
        if not isinstance(value,dict) or set(value)-{'screen','kind','clock_id','category','page','asset'}:
            raise ValueError('선택 정보를 확인해 주세요.')
        screen=value.get('screen')
        if type(screen) is not int or screen not in (3,4,5):
            raise ValueError('3·4·5번 화면만 선택할 수 있어요.')
        if value.get('kind')=='native':
            if type(value.get('clock_id')) is not int:
                raise ValueError('공식 목록의 화면을 선택해 주세요.')
            rows=self.catalog(value.get('category'),value.get('page',1))['items']
            selected=next((x for x in rows if x['clock_id']==value.get('clock_id')),None)
            if selected is None:
                raise ValueError('공식 목록의 화면을 선택해 주세요.')
        elif value.get('kind')=='gif':
            selected=next((x for x in self.gifs() if x['asset']==value.get('asset')),None)
            if selected is None:
                raise ValueError('등록된 디붐 GIF를 선택해 주세요.')
        else:
            raise ValueError('화면 종류를 확인해 주세요.')
        choice=self.normalize(selected,screen)
        with self.lock:
            if self.pending_file.exists() and self.pending is None:
                raise ValueError('이전 미리보기를 복원 중이에요. 잠시 후 다시 선택해 주세요.')
            original=self.current(screen)
            if self.pending and self.pending['choice']['screen']==screen:
                original=self.pending['original']
            elif self.pending:
                self._restore()
            pending={'id':secrets.token_hex(12),'choice':choice,'original':original,
                     'until':time.monotonic()+60}
            # Record the return target before the reversible device operation.
            self.save_json(self.pending_file,{'screen':screen})
            self.pending=pending
            try:
                self.render(choice)
            except Exception:
                self._restore()
                raise
            self.error=''
            return self.status()

    def _restore(self):
        if not self.pending:
            return
        # Saved configuration wins over a stale preview if another window saved.
        original=self.current(self.pending['original']['screen'])
        self.render(original)
        self.pending=None
        self.pending_file.unlink(missing_ok=True)
        self.error=''

    def cancel(self, preview_id):
        with self.lock:
            if self.pending and preview_id!=self.pending['id']:
                raise ValueError('다른 미리보기가 열려 있어요. 화면을 다시 확인해 주세요.')
            self._restore()
            return self.status()

    def commit(self, preview_id):
        with self.lock:
            if not self.pending or preview_id!=self.pending['id'] or time.monotonic()>=self.pending['until']:
                raise ValueError('미리보기가 끝났어요. 다시 보고 선택해 주세요.')
            choice=self.pending['choice']
            current=self.current(choice['screen'])
            if current!=self.pending['original']:
                self._restore()
                raise ValueError('저장된 화면이 바뀌어 새 설정을 유지했어요. 다시 선택해 주세요.')
            config=json.loads(self.config_path.read_text(encoding='utf-8'))
            row=next(x for x in config['native_screens'] if x['screen']==choice['screen']-1)
            row.clear()
            row.update({k:v for k,v in choice.items() if k not in ('kind','screen')},
                       screen=choice['screen']-1,mode=choice['kind'],selected_by='office-picker')
            self.save_json(self.config_path,config)
            self.display.config['native_screens']=config['native_screens']
            self.pending=None
            self.pending_file.unlink(missing_ok=True)
            return self.status()

    def reconcile_connection(self):
        """Restore saved panel 3 at startup/observed reconnect, outside previews."""
        now=time.monotonic()
        if now<self.next_connection_probe:
            return
        resumed=self.next_connection_probe>0 and now-self.next_connection_probe>15
        self.next_connection_probe=now+3
        try:
            settings=self.display.command({'Command':'Channel/GetAllConf'})
        except (OSError,RuntimeError,ValueError):
            with self.lock:
                self.connection_recovery.update(connected=False,last_checked=time.time(),acknowledged=False,
                                                error='기기 재연결을 기다리고 있어요.')
                self.restore_saved_screen=True
            return
        with self.lock:
            reconnected=self.connection_recovery['connected'] is False
            light=settings.get('LightSwitch')
            woke=self.connection_recovery['light_switch']==0 and light==1
            self.connection_recovery.update(connected=True,last_checked=time.time(),
                light_switch=light if light in (0,1) else None)
            if reconnected or resumed or woke:
                self.restore_saved_screen=True
                # Re-send unchanged team GIFs through existing freshness/rate limits.
                with self.display.lock:
                    self.display.continuous_signatures.clear()
                    self.display.continuous_candidates.clear()
            if light==0 or not self.restore_saved_screen or self.pending or self.pending_file.exists():
                return
            try:
                choice=self.current(3)
                self.render(choice)
                self.restore_saved_screen=False
                self.connection_recovery.update(last_restored=time.time(),acknowledged=True,
                    choice=choice,error='')
            except (OSError,RuntimeError,ValueError,KeyError):
                self.connection_recovery.update(acknowledged=False,
                    error='저장된 3번 화면 복원을 기다리고 있어요.')

    def watch(self):
        recover_at=0
        while not self.display.stop.wait(.5):
            with self.lock:
                if self.pending is None and self.pending_file.exists() and time.monotonic()>=recover_at:
                    try:
                        old=json.loads(self.pending_file.read_text(encoding='utf-8'))
                        self.render(self.current(old['screen']))
                        self.pending_file.unlink()
                        self.error=''
                    except Exception:
                        self.error='이전 미리보기를 복원하지 못했어요. 기기 연결을 확인해 주세요.'
                        recover_at=time.monotonic()+5
                if self.pending and time.monotonic()>=self.pending['until'] and time.monotonic()>=self.pending.get('restore_after',0):
                    try:
                        self._restore()
                    except Exception:
                        self.error='원래 화면 복원을 기다리고 있어요. 기기 연결을 확인해 주세요.'
                        self.pending['restore_after']=time.monotonic()+5
            self.reconcile_connection()

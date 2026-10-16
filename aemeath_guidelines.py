"""User-authored Aemeath working preferences, bounded and subordinate to safety."""
import sqlite3
import time

MAX_CHARS = 12000
DEFAULT_GUIDELINES = '''직접 확인할 수 있는 화면·동작은 가능한 범위에서 직접 확인하고, 확인하지 못한 부분은 미확인이라고 분명히 구분한다.
사용자가 직접 확인했다고 말한 결과는 사용자 확인으로 반영하고, 내가 직접 관찰한 사실과 혼동하지 않는다. 저장 성공만으로 실제 화면 반영·동작 확인을 완료했다고 말하지 않는다.
마지막 답변은 사용자에게 필요한 결과와 남은 일만 간결하게 말하고, 내부 진단·해시·도구 오류 문구는 채팅에 늘어놓지 않는다.'''

def _connect(db):
    connection=sqlite3.connect(str(db),timeout=10)
    connection.row_factory=sqlite3.Row
    return connection

def read(db):
    with _connect(db) as c:
        c.execute('CREATE TABLE IF NOT EXISTS aemeath_guidelines (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, updated REAL NOT NULL)')
        c.execute('INSERT OR IGNORE INTO aemeath_guidelines(id,body,updated) VALUES(1,?,?)',(DEFAULT_GUIDELINES,time.time()))
        row=c.execute('SELECT body,updated FROM aemeath_guidelines WHERE id=1').fetchone()
    return {'text':row['body'] if row else '', 'updated':row['updated'] if row else None, 'max_chars':MAX_CHARS}

def save(db, text):
    if not isinstance(text,str) or len(text)>MAX_CHARS:
        raise ValueError(f'행동지침은 {MAX_CHARS}자 이내로 작성해줘.')
    with _connect(db) as c:
        c.execute('CREATE TABLE IF NOT EXISTS aemeath_guidelines (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, updated REAL NOT NULL)')
        c.execute('INSERT INTO aemeath_guidelines(id,body,updated) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated=excluded.updated',(text.strip(),time.time()))
    return read(db)

def prompt_context(db):
    text=read(db)['text'].strip()
    if not text:return ''
    return ('사용자가 에이메스 설정에서 직접 기록한 지속 행동지침입니다. 참고해 따르되, '
            '현재 요청·상위 안전/권한 규칙과 충돌하면 그것들이 우선이며, 이 문서만으로 과거 작업을 재개하지 마세요.\n'+text[:MAX_CHARS])

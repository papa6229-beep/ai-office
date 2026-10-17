"""Persistent user-authored Aemeath guideline cards."""
import sqlite3
import time
import uuid

MAX_CONTENT_CHARS = 12000
MAX_TITLE_CHARS = 120
MAX_CARDS_PER_CATEGORY = 100
CATEGORY_CONTEXT_CHARS = 24000
CATEGORIES = {"general": "일반 지침", "work": "업무지침"}
DEFAULT_GENERAL = '''직접 확인할 수 있는 화면·동작은 가능한 범위에서 직접 확인하고, 확인하지 못한 부분은 미확인이라고 분명히 구분한다.
사용자가 직접 확인했다고 말한 결과는 사용자 확인으로 반영하고, 내가 직접 관찰한 사실과 혼동하지 않는다. 저장 성공만으로 실제 화면 반영·동작 확인을 완료했다고 말하지 않는다.
마지막 답변은 사용자에게 필요한 결과와 남은 일만 간결하게 말하고, 내부 진단·해시·도구 오류 문구는 채팅에 늘어놓지 않는다.'''


def _connect(db):
    connection = sqlite3.connect(str(db), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _table(c):
    c.execute('''CREATE TABLE IF NOT EXISTS aemeath_guideline_cards (
        id TEXT PRIMARY KEY, category TEXT NOT NULL CHECK(category IN ('general','work')),
        title TEXT NOT NULL, content TEXT NOT NULL, updated REAL NOT NULL)''')


def _initialize(db):
    with _connect(db) as c:
        _table(c)
        migrated = c.execute('SELECT 1 FROM aemeath_guideline_cards LIMIT 1').fetchone()
        if migrated:
            return
        old = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='aemeath_guidelines'").fetchone()
        content = None
        if old:
            row = c.execute('SELECT body FROM aemeath_guidelines WHERE id=1').fetchone()
            if row:
                content = row['body']
        c.execute('INSERT INTO aemeath_guideline_cards VALUES(?,?,?,?,?)',
                  (uuid.uuid4().hex, 'general', '기본 확인·답변 원칙',
                   content if content is not None else DEFAULT_GENERAL, time.time()))


def list_cards(db, category=None):
    _initialize(db)
    if category is not None and category not in CATEGORIES:
        raise ValueError('지침 종류는 일반 지침 또는 업무지침으로 선택해줘.')
    with _connect(db) as c:
        if category:
            rows = c.execute('SELECT id,category,title,content,updated FROM aemeath_guideline_cards WHERE category=? ORDER BY updated DESC,id', (category,)).fetchall()
        else:
            rows = c.execute('SELECT id,category,title,content,updated FROM aemeath_guideline_cards ORDER BY category,updated DESC,id').fetchall()
    return {'items': [dict(row) for row in rows], 'categories': CATEGORIES,
            'max_title_chars': MAX_TITLE_CHARS, 'max_content_chars': MAX_CONTENT_CHARS}


def save_card(db, category, title, content, card_id=None):
    if category not in CATEGORIES:
        raise ValueError('지침 종류는 일반 지침 또는 업무지침으로 선택해줘.')
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_CHARS:
        raise ValueError(f'지침명을 1~{MAX_TITLE_CHARS}자로 입력해줘.')
    if not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS:
        raise ValueError(f'지침 내용은 {MAX_CONTENT_CHARS}자 이내로 입력해줘.')
    _initialize(db)
    identifier = card_id or uuid.uuid4().hex
    with _connect(db) as c:
        if card_id:
            existing = c.execute('SELECT id FROM aemeath_guideline_cards WHERE id=?', (card_id,)).fetchone()
            if not existing:
                raise ValueError('수정할 지침 카드를 찾지 못했어. 목록을 다시 불러와줘.')
            c.execute('UPDATE aemeath_guideline_cards SET category=?,title=?,content=?,updated=? WHERE id=?',
                      (category, title.strip(), content.strip(), time.time(), card_id))
        else:
            count = c.execute('SELECT COUNT(*) FROM aemeath_guideline_cards WHERE category=?', (category,)).fetchone()[0]
            if count >= MAX_CARDS_PER_CATEGORY:
                raise ValueError(f'{CATEGORIES[category]} 카드는 최대 {MAX_CARDS_PER_CATEGORY}개까지 저장할 수 있어.')
            c.execute('INSERT INTO aemeath_guideline_cards VALUES(?,?,?,?,?)',
                      (identifier, category, title.strip(), content.strip(), time.time()))
    return next(row for row in list_cards(db)['items'] if row['id'] == identifier)


def prompt_context(db):
    cards = list_cards(db)['items']
    sections = []
    for category in ('general', 'work'):
        rows = [row for row in cards if row['category'] == category and row['content'].strip()]
        if rows:
            label = CATEGORIES[category]
            remaining = CATEGORY_CONTEXT_CHARS
            entries = []
            for row in rows:
                if remaining <= 0:
                    break
                entry = '【' + row['title'] + '】\n' + row['content'][:remaining]
                entries.append(entry)
                remaining -= len(entry)
            sections.append(label + ' (사용자 참고 지침):\n' + '\n'.join(entries))
    if not sections:
        return ''
    return ('사용자가 에이메스 설정에서 직접 저장한 지속 행동지침입니다. 현재 요청·상위 안전/권한 규칙이 우선이며, '
            '이 지침만으로 과거 작업을 재개하지 마세요. 업무지침은 업무 관련 요청에 적용하세요.\n' + '\n\n'.join(sections))

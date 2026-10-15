"""Atomic preferences for the work desk; never modifies desktop entries."""
import json
import pathlib
import threading

STATE = pathlib.Path(__file__).resolve().parent / 'desktop_state.json'
LOCK = threading.Lock()
MAX_PINS = 30

def _read():
    if not STATE.exists():
        return {'pinned_ids': []}
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        pins = data.get('pinned_ids') if isinstance(data, dict) else None
        if not isinstance(pins, list) or len(pins) > MAX_PINS or any(not isinstance(p, str) or len(p) > 100 for p in pins):
            raise ValueError()
        return data
    except (OSError, ValueError):
        raise ValueError('즐겨찾기 설정을 읽을 수 없습니다. 기존 설정을 보존했습니다.') from None

def load():
    with LOCK:
        return _read()

def pin(item_id, enabled):
    if not isinstance(item_id, str) or not item_id or len(item_id) > 100 or not isinstance(enabled, bool):
        raise ValueError('즐겨찾기 항목을 확인하세요.')
    with LOCK:
        data = _read()
        pins = list(dict.fromkeys(data['pinned_ids']))
        if enabled and item_id not in pins:
            if len(pins) >= MAX_PINS:
                raise ValueError('즐겨찾기는 최대 30개까지 고정할 수 있습니다.')
            pins.append(item_id)
        elif not enabled:
            pins = [p for p in pins if p != item_id]
        data['pinned_ids'] = pins
        temporary = STATE.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(STATE)
        return data

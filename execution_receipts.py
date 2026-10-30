"""Bounded local receipt writes and content-free execution measurements.

Retries apply only to an already prepared local metadata replacement, never to
the external action whose result is being recorded.
"""
import json
import os
from pathlib import Path
import tempfile
import time

REVISION = '2026-10-28-metadata-v1'


def replace(temporary, target):
    for attempt in range(10):
        try:
            Path(temporary).replace(target)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 9:
                raise
            time.sleep(.04 * (attempt + 1))


def write_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        replace(temporary, path)
    finally:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass


def observe(timings, stage, started):
    elapsed = max(0, round((time.monotonic()-started)*1000))
    row = timings.setdefault(stage, {'calls': 0, 'elapsed_ms': 0})
    row['calls'] += 1
    row['elapsed_ms'] += elapsed
    import execution_router
    execution_router.record_time(stage, elapsed)

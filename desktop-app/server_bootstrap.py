"""Start existing Office hidden; log streams belong to the server, not its UI."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent.parent

if __name__ == '__main__':
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ['AI_OFFICE_NO_BROWSER'] = '1'
    with (ROOT / 'startup.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            runpy.run_path(str(ROOT / 'app.py'), run_name='__main__')
        except BaseException:
            import traceback
            traceback.print_exc()
            raise

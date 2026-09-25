"""
Safe access to the daily log files.

The poller appends to today's file while the UI can rewrite it (mark personal,
edit tags, delete). Both take LOG_LOCK, and rewrites go through a temp file +
atomic rename, so an append is never lost to a half-finished rewrite and a
crash never leaves a truncated log. (A poller running as a *separate process*
can't share the lock; the atomic replace still keeps the file intact, so stop
it before running bulk rewrites such as `python -m logger.redact_logs --apply`.)
"""

import os
import tempfile
import threading
from pathlib import Path

LOG_LOCK = threading.RLock()


def rewrite_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

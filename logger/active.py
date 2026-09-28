"""
Active commessa — the commessa the user is currently working on, chosen from
the UI (or set by hand) and read by the poller so every entry it writes
carries a `commessa` field directly. This is what lets the daily summary
group time by an explicit choice instead of only guessing from keywords in
window titles, branches and commit messages (see summarizer/sessions.py).

State lives in a small JSON file, like the tracking pause (logger/pause.py):

    {"name": "Backend revamp", "since": "2026-09-28T09:03:11"}

`name` null/absent means "no active commessa" — a normal state (e.g. right
after the app starts and before the user has picked one).
"""

import json
from datetime import datetime
from pathlib import Path

ACTIVE_FILE = Path.home() / '.worklog' / 'active.json'


def _read() -> dict | None:
    try:
        data = json.loads(ACTIVE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def active_commessa() -> str | None:
    """The name of the commessa currently active, or None."""
    name = (_read() or {}).get('name')
    return name or None


def active_state() -> dict:
    """{'name': str|None, 'since': iso|None}"""
    data = _read() or {}
    return {'name': data.get('name') or None, 'since': data.get('since')}


def set_active(name: str, now: datetime | None = None) -> dict:
    """Make `name` the active commessa (blank clears it)."""
    name = (name or '').strip()
    now = now or datetime.now()
    data = {'name': name or None, 'since': now.isoformat(timespec='seconds')}
    ACTIVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(json.dumps(data))
    return active_state()


def clear_active() -> dict:
    Path(ACTIVE_FILE).unlink(missing_ok=True)
    return active_state()

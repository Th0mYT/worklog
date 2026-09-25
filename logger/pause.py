"""
Manual tracking pause — the one reliable way to keep something out of the log
that no rule can recognise (a video on a normally-work domain, a personal chat…).

State lives in a small JSON file so the UI process and a separate CLI poller
agree on it:

    {"since": "2026-09-24T14:03:11", "until": "2026-09-24T14:33:11" | null}

`until` null means "until resumed". An expired pause is treated as over.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from logger.timeutil import parse_ts

PAUSE_FILE = Path.home() / '.worklog' / 'paused.json'


def _read() -> dict | None:
    try:
        data = json.loads(PAUSE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def pause_state(now: datetime | None = None) -> dict:
    """{'paused': bool, 'since': iso|None, 'until': iso|None}"""
    now = now or datetime.now()
    data = _read()
    if not data:
        return {'paused': False, 'since': None, 'until': None}
    until = parse_ts(data.get('until'))
    if until and until <= now:
        return {'paused': False, 'since': None, 'until': None}
    return {'paused': True, 'since': data.get('since'), 'until': data.get('until')}


def is_paused(now: datetime | None = None) -> bool:
    return pause_state(now)['paused']


def pause(minutes: int = 0, now: datetime | None = None) -> dict:
    """Pause tracking for `minutes` (0 = until resumed)."""
    now = now or datetime.now()
    data = {
        'since': now.isoformat(timespec='seconds'),
        'until': (now + timedelta(minutes=minutes)).isoformat(timespec='seconds') if minutes > 0 else None,
    }
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(json.dumps(data))
    return pause_state(now)


def resume() -> dict:
    Path(PAUSE_FILE).unlink(missing_ok=True)
    return pause_state()

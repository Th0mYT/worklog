"""
Timestamp helpers shared by the logger and the summarizer.

Log entries come from different writers and use different ISO flavours:

  poller       2026-09-24T09:14:15
  git (old)    2026-09-24 09:40:54 +0200
  git (new)    2026-09-24T09:40:54

Sorting those as raw strings is wrong (' ' < 'T' puts every commit before
every snapshot), so anything that orders or subtracts timestamps goes through
parse_ts(), which returns naive *local* datetimes.
"""

import re
from datetime import datetime

_TS_RE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}(?::\d{2})?)'
    r'(?:\.\d+)?\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?$'
)


def parse_ts(value) -> datetime | None:
    """Parse a log timestamp into a naive local datetime, or None if invalid."""
    if not value:
        return None
    m = _TS_RE.match(str(value).strip())
    if not m:
        return None
    time_part = m.group('time')
    if len(time_part) == 5:
        time_part += ':00'
    tz = m.group('tz')
    try:
        if tz:
            if tz == 'Z':
                tz = '+00:00'
            elif ':' not in tz:
                tz = tz[:3] + ':' + tz[3:]
            dt = datetime.fromisoformat(f"{m.group('date')}T{time_part}{tz}")
            return dt.astimezone().replace(tzinfo=None)
        return datetime.fromisoformat(f"{m.group('date')}T{time_part}")
    except ValueError:
        return None


def normalize_ts(value) -> str:
    """Return `value` as naive local ISO (seconds precision); unchanged if unparseable."""
    dt = parse_ts(value)
    return dt.isoformat(timespec='seconds') if dt else str(value or '')


def entry_time(entry: dict) -> datetime:
    """Sort key for a log entry — entries without a valid ts sort first."""
    return parse_ts(entry.get('ts')) or datetime.min

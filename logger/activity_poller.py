"""
Activity poller — captures the frontmost macOS app every N seconds
and appends a structured entry to a daily JSONL log file.

Log location: Config.LOGS_DIR / YYYY-MM-DD.jsonl

Each entry:
  {
    "ts":         "2026-06-18T09:05:00",
    "app":        "Cursor",
    "window":     "worklog – activity_poller.py",
    "type":       "coding",
    "tab_title":  "...",   # browser only
    "url":        "..."    # browser only
  }

The poller pauses automatically after Config.INACTIVITY_TIMEOUT seconds of
no keyboard/mouse activity, and resumes as soon as input is detected again.
"""

import ctypes
import ctypes.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from config import CATEGORIES, MEETING_WINDOW_SIGNALS, Config
from logger.browser_reader import get_tab_info

# ---------------------------------------------------------------------------
# System idle time — no Input Monitoring permission required
# ---------------------------------------------------------------------------

def _system_idle_seconds() -> float:
    """Seconds since last keyboard/mouse event via CoreGraphics (no special permissions)."""
    try:
        _lib = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library('Quartz')
            or '/System/Library/Frameworks/Quartz.framework/Quartz'
        )
        fn = _lib.CGEventSourceSecondsSinceLastEventType
        fn.restype = ctypes.c_double
        fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
        # kCGEventSourceStateHIDSystemState=1, kCGAnyInputEventType=0xFFFFFFFF
        return float(fn(1, 0xFFFFFFFF))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Tag index — repo name → tags, built once at startup
# ---------------------------------------------------------------------------

def _build_tag_index() -> dict[str, list[str]]:
    """Map lowercase repo-name → tags for window-title matching at poll time."""
    index: dict[str, list[str]] = {}

    for path in Config.GIT_REPOS:
        tags = Config.GIT_REPO_TAGS.get(path, [])
        if tags:
            index[Path(path).expanduser().name.lower()] = tags

    for workspace in Config.GIT_WORKSPACES:
        ws_tags = Config.GIT_REPO_TAGS.get(workspace, [])
        if not ws_tags:
            continue
        try:
            ws_path = Path(workspace).expanduser()
            for sub in sorted(ws_path.iterdir()):
                if sub.is_dir() and (sub / '.git').exists():
                    index[sub.name.lower()] = ws_tags
        except OSError:
            pass

    return index


def _tags_for_window(window: str, tag_index: dict[str, list[str]]) -> list[str]:
    """Return the first matching tag list whose repo name appears in the window title."""
    w = window.lower()
    for name, tags in tag_index.items():
        if name in w:
            return tags
    return []

# ---------------------------------------------------------------------------
# macOS window detection (System Events — works for all apps, no scripting needed)
# ---------------------------------------------------------------------------

_ACTIVE_WINDOW_SCRIPT = '''
tell application "System Events"
    set frontProc to first application process whose frontmost is true
    set appName to name of frontProc
    set winTitle to ""
    try
        set winTitle to name of front window of frontProc
    end try
end tell
return appName & "|||" & winTitle
'''


def get_active_window() -> tuple[str, str]:
    """Return (app_name, window_title) of the frontmost macOS window."""
    try:
        result = subprocess.run(
            ['osascript', '-e', _ACTIVE_WINDOW_SCRIPT],
            capture_output=True, text=True, timeout=5,
        )
        raw = result.stdout.strip()
        if '|||' in raw:
            app, window = raw.split('|||', 1)
            return app.strip(), window.strip()
    except Exception:
        pass
    return '', ''


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(app: str, window: str) -> str:
    app_lower = app.lower()
    for category, apps in CATEGORIES.items():
        if any(app_lower == a.lower() for a in apps):
            if category == 'meeting':
                return _resolve_meeting(app, window)
            return category
    return 'other'


def _resolve_meeting(app: str, window: str) -> str:
    """Distinguish an active voice/video call from regular chat usage."""
    w = window.lower()
    if any(signal in w for signal in MEETING_WINDOW_SIGNALS):
        return 'meeting'
    # Discord: a voice channel window title typically starts with a '#' or contains 'call'
    if app == 'Discord' and ('#' in window or 'call' in w):
        return 'meeting'
    return 'communication'


# ---------------------------------------------------------------------------
# Core poll
# ---------------------------------------------------------------------------

def poll_once(
    log_dir: Path,
    tag_index: dict[str, list[str]] | None = None,
) -> None:
    app, window = get_active_window()
    if not app:
        return

    entry: dict = {
        'ts': datetime.now().isoformat(timespec='seconds'),
        'app': app,
        'window': window,
        'type': _classify(app, window),
    }

    # Enrich browser entries with the actual tab title and URL
    if entry['type'] == 'browser':
        tab = get_tab_info(app)
        if tab:
            entry['tab_title'] = tab[0]
            entry['url'] = tab[1]

    # Attach tags when the window title reveals which configured repo is active
    if tag_index and window:
        tags = _tags_for_window(window, tag_index)
        if tags:
            entry['tags'] = tags

    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime('%Y-%m-%d')
    log_file = log_dir / f'{day}.jsonl'
    with log_file.open('a') as f:
        f.write(json.dumps(entry) + '\n')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_IDLE_CHECK_INTERVAL = 10  # seconds between checks while paused


def run(interval: int = Config.POLL_INTERVAL,
        inactivity_timeout: int = Config.INACTIVITY_TIMEOUT) -> None:
    log_dir = Path(Config.LOGS_DIR)
    tag_index = _build_tag_index()
    if tag_index:
        print(f'[worklog] tag index: {len(tag_index)} repo(s) mapped to tags', flush=True)
    print(
        f'[worklog] poller started  interval={interval}s'
        f'  inactivity_timeout={inactivity_timeout}s  logs={log_dir}',
        flush=True,
    )
    was_idle = False
    while True:
        idle = _system_idle_seconds() > inactivity_timeout
        if idle:
            if not was_idle:
                was_idle = True
                print(
                    f'[worklog] no activity for {inactivity_timeout}s — pausing poller',
                    flush=True,
                )
            time.sleep(_IDLE_CHECK_INTERVAL)
            continue

        if was_idle:
            was_idle = False
            print('[worklog] activity detected — resuming poller', flush=True)

        try:
            poll_once(log_dir, tag_index)
        except Exception as e:
            print(f'[worklog] poll error: {e}', file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='worklog activity poller')
    parser.add_argument('--interval', type=int, default=Config.POLL_INTERVAL,
                        help='Polling interval in seconds (default: %(default)s)')
    parser.add_argument('--once', action='store_true',
                        help='Capture a single snapshot and exit (useful for testing)')
    args = parser.parse_args()

    if args.once:
        poll_once(Path(Config.LOGS_DIR), _build_tag_index())
    else:
        run(args.interval)

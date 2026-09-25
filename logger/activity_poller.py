"""
Activity poller — watches the frontmost macOS app/window and appends
structured entries to a daily JSONL log file.

Log location: Config.LOGS_DIR / YYYY-MM-DD.jsonl

The poller checks the frontmost window every Config.SAMPLE_INTERVAL seconds but
only writes when something changed, plus a heartbeat every Config.POLL_INTERVAL
seconds so a long stretch in one window still shows up. Each entry is a point
"at ts, this was in front"; durations are derived later (see summarizer/sessions.py).

Activity entry:
  {
    "ts":       "2026-06-18T09:05:00",
    "app":      "PyCharm",
    "window":   "worklog – activity_poller.py",
    "type":     "coding",
    "project":  "worklog",                 # IDE project or terminal repo
    "file":     "activity_poller.py",
    "branch":   "feature/KH-682-fix",      # configured repos only
    "wip_files": ["logger/x.py"],          # heartbeats only
    "tags":     ["personal"]
  }

Browser entries never keep more than the privacy rules allow:
  work domain     → domain, tab_title, url (no query string / fragment)
  other domain    → domain only
  private window  → {"excluded": true, "reason": "private"} and nothing else

Marker entries delimit time the user was not there:
  {"ts": "...", "marker": "idle_start" | "idle_end" | "pause" | "resume" | "stop" | "start"}

The poller pauses automatically after Config.INACTIVITY_TIMEOUT seconds of no
keyboard/mouse activity, resumes as soon as input is detected, and honours the
manual pause (logger/pause.py).
"""

import ctypes
import ctypes.util
import json
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from config import MEETING_WINDOW_SIGNALS, Config
from logger.browser_reader import get_tab_info
from logger.extractors import (
    RepoInfo,
    build_repo_index,
    classify_domain,
    domain_of,
    find_repo_root,
    keeps_detail,
    match_repo,
    parse_ide_title,
    path_from_title,
    strip_url,
)
from logger.git_info import current_branch, wip_files
from logger.logio import LOG_LOCK
from logger.pause import is_paused, pause_state
from logger.terminal_reader import terminal_cwd
from logger.timeutil import parse_ts
from logger.window_info import FrontWindow, get_front_window

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
# Classification
# ---------------------------------------------------------------------------

def _classify(app: str, window: str) -> str:
    app_lower = app.lower()
    for category, apps in Config.CATEGORIES.items():
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
# Building one entry from what is in front right now
# ---------------------------------------------------------------------------

def _browser_entry(app: str, tab_fn) -> dict:
    tab = tab_fn(app)
    if tab and tab.private:
        # Nothing about a private window — not even its domain — is recorded.
        return {'app': app, 'window': '', 'type': 'browser', 'excluded': True, 'reason': 'private'}

    entry: dict = {'app': app, 'window': '', 'type': 'browser'}
    domain = domain_of(tab.url) if tab else ''
    if not domain:
        return entry

    site_class = classify_domain(domain, Config.BROWSER_RULES)
    entry['domain'] = domain
    entry['site_class'] = site_class
    if keeps_detail(site_class, Config.BROWSER_UNKNOWN):
        entry['tab_title'] = tab.title
        entry['url'] = strip_url(tab.url)
    return entry


def _attach_project(entry: dict, front: FrontWindow, index: dict[str, RepoInfo],
                    cwd_fn, with_wip: bool) -> RepoInfo | None:
    """Fill project/file/branch/tags for a coding entry. Returns the matched repo, if any."""
    project, file = parse_ide_title(front.app, front.window)
    repo = match_repo(index, project, front.window)

    if not project and not repo:
        # Terminals: the title rarely says anything, the cwd usually does.
        cwd = cwd_fn(front.app) or path_from_title(front.window)
        root = find_repo_root(cwd)
        if root:
            project = Path(root).name
            repo = match_repo(index, project)

    if repo:
        project = repo.name
    if project:
        entry['project'] = project
    if file:
        entry['file'] = file
    if repo:
        branch = current_branch(repo.path)
        if branch:
            entry['branch'] = branch
        if with_wip:
            wip = wip_files(repo.path)
            if wip:
                entry['wip_files'] = wip
        if repo.tags:
            entry['tags'] = list(repo.tags)
    return repo


def build_entry(front: FrontWindow, index: dict[str, RepoInfo], *, with_wip: bool = False,
                tab_fn=get_tab_info, cwd_fn=terminal_cwd) -> dict | None:
    """Turn the frontmost window into a log entry (without `ts`); None if nothing is in front."""
    app = front.app
    if not app:
        return None
    if app.lower() in {a.lower() for a in Config.IGNORE_APPS}:
        # Recorded as excluded (not skipped) so it ends the previous entry's
        # duration instead of silently extending it.
        return {'app': app, 'window': '', 'type': 'other', 'excluded': True, 'reason': 'ignored-app'}

    kind = _classify(app, front.window)
    if kind == 'browser':
        return _browser_entry(app, tab_fn)

    entry: dict = {'app': app, 'window': front.window, 'type': kind}
    if kind == 'coding':
        _attach_project(entry, front, index, cwd_fn, with_wip)
    elif kind not in Config.REDACT_TITLE_TYPES:
        repo = match_repo(index, '', front.window)
        if repo and repo.tags:
            entry['tags'] = list(repo.tags)
    if kind in Config.REDACT_TITLE_TYPES:
        entry['window'] = ''
    return entry


_SIG_KEYS = ('app', 'window', 'type', 'domain', 'tab_title', 'url', 'project', 'file', 'excluded')


def _signature(entry: dict) -> tuple:
    return tuple(entry.get(k) for k in _SIG_KEYS)


# ---------------------------------------------------------------------------
# Tracker — decides when to write
# ---------------------------------------------------------------------------

class Tracker:
    def __init__(self, log_dir: Path, *, front_fn=get_front_window, entry_fn=build_entry,
                 now_fn=datetime.now, paused_fn=is_paused) -> None:
        self.log_dir = Path(log_dir)
        self._front_fn = front_fn
        self._entry_fn = entry_fn
        self._now = now_fn
        self._paused = paused_fn
        self._state = 'active'          # active | idle | paused
        self._last_sig: tuple | None = None
        self._last_write: datetime | None = None
        self._index: dict[str, RepoInfo] = {}
        self._index_key: str | None = None

    # -- output ----------------------------------------------------------------

    def _append(self, entry: dict, ts: datetime) -> None:
        entry = {'ts': ts.isoformat(timespec='seconds'), **entry}
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with LOG_LOCK, (self.log_dir / f'{ts:%Y-%m-%d}.jsonl').open('a') as f:
            f.write(json.dumps(entry) + '\n')

    def mark(self, event: str, ts: datetime | None = None, **extra) -> None:
        self._append({'marker': event, **extra}, ts or self._now())

    # -- repo index (rebuilt when the repo config changes) -----------------------

    def _repo_index(self) -> dict[str, RepoInfo]:
        key = json.dumps([Config.GIT_REPOS, Config.GIT_WORKSPACES, Config.GIT_REPO_TAGS], sort_keys=True)
        if key != self._index_key:
            self._index = build_repo_index(Config.GIT_REPOS, Config.GIT_WORKSPACES, Config.GIT_REPO_TAGS)
            self._index_key = key
        return self._index

    # -- state transitions -------------------------------------------------------

    def enter_idle(self, idle_seconds: float) -> None:
        if self._state == 'idle':
            return
        # The gap starts when the user last touched the machine, not now.
        self.mark('idle_start', self._now() - timedelta(seconds=idle_seconds))
        self._state = 'idle'

    def leave_idle(self) -> None:
        if self._state == 'idle':
            self.mark('idle_end')
            self._state, self._last_sig = 'active', None

    def enter_pause(self, since: str | None = None) -> None:
        if self._state == 'paused':
            return
        self.mark('pause', parse_ts(since) or self._now())
        self._state = 'paused'

    def leave_pause(self) -> None:
        if self._state == 'paused':
            self.mark('resume')
            self._state, self._last_sig = 'active', None

    def close(self, idle_seconds: float = 0.0) -> None:
        """Called when the poller stops; closes the open span if one is running."""
        if self._state == 'active':
            self.mark('stop', self._now() - timedelta(seconds=idle_seconds))

    # -- one sample --------------------------------------------------------------

    def tick(self) -> bool:
        """Sample the frontmost window; write if it changed or a heartbeat is due."""
        now = self._now()
        due = (self._last_write is None
               or (now - self._last_write).total_seconds() >= Config.POLL_INTERVAL)
        entry = self._entry_fn(self._front_fn(), self._repo_index(), with_wip=due)
        if entry is None:
            return False
        sig = _signature(entry)
        if sig == self._last_sig and not due:
            return False
        if self._paused():  # re-checked at the last moment: pause must win the race
            return False
        self._append(entry, now)
        self._last_sig, self._last_write = sig, now
        return True


# ---------------------------------------------------------------------------
# Main loop (shared by the CLI and the UI's poller thread)
# ---------------------------------------------------------------------------

_IDLE_CHECK_INTERVAL = 10  # seconds between checks while idle
_PAUSE_CHECK_INTERVAL = 2  # seconds between checks while manually paused


def _wait_until_next_sample(stop: threading.Event, seconds: float) -> None:
    """Sleep for the sample interval, but wake early when the user pauses tracking.

    Without this a pause shorter than the interval would go unnoticed and the
    paused minutes would be credited to whatever was in front before it.
    """
    deadline = time.monotonic() + seconds
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or is_paused():
            return
        stop.wait(min(_PAUSE_CHECK_INTERVAL, remaining))


def run_loop(stop: threading.Event, log_dir: Path | None = None, tracker: Tracker | None = None) -> None:
    tracker = tracker or Tracker(log_dir or Path(Config.LOGS_DIR))
    # The heartbeat goes on record so the summarizer can size samples correctly
    # even when it runs with a different config than this poller.
    tracker.mark('start', heartbeat=Config.POLL_INTERVAL)
    try:
        while not stop.is_set():
            state = pause_state()
            if state['paused']:
                tracker.enter_pause(state.get('since'))
                stop.wait(_PAUSE_CHECK_INTERVAL)
                continue
            tracker.leave_pause()

            idle = _system_idle_seconds()
            if idle > Config.INACTIVITY_TIMEOUT:
                tracker.enter_idle(idle)
                stop.wait(_IDLE_CHECK_INTERVAL)
                continue
            tracker.leave_idle()

            try:
                tracker.tick()
            except Exception as e:  # never let one bad sample kill the poller
                print(f'[worklog] poll error: {e}', file=sys.stderr, flush=True)
            _wait_until_next_sample(stop, Config.SAMPLE_INTERVAL)
    finally:
        tracker.close(_system_idle_seconds())


def poll_once(log_dir: Path) -> bool:
    """Capture a single snapshot (useful for testing). Returns True if an entry was written."""
    return Tracker(log_dir).tick()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(interval: int = Config.POLL_INTERVAL,
        inactivity_timeout: int = Config.INACTIVITY_TIMEOUT,
        sample_interval: int = Config.SAMPLE_INTERVAL) -> None:
    Config.POLL_INTERVAL = interval
    Config.INACTIVITY_TIMEOUT = inactivity_timeout
    Config.SAMPLE_INTERVAL = max(5, sample_interval)
    log_dir = Path(Config.LOGS_DIR)
    print(
        f'[worklog] poller started  sample={Config.SAMPLE_INTERVAL}s  heartbeat={interval}s'
        f'  inactivity_timeout={inactivity_timeout}s  logs={log_dir}',
        flush=True,
    )
    from logger.window_info import health
    problem = health().get('problem')
    if problem:
        print(f'[worklog] WARNING: {problem}', file=sys.stderr, flush=True)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run_loop(stop, log_dir)
    print('[worklog] poller stopped', flush=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description='worklog activity poller')
    parser.add_argument('--interval', type=int, default=Config.POLL_INTERVAL,
                        help='Heartbeat: max seconds between entries when nothing changes (default: %(default)s)')
    parser.add_argument('--sample-interval', type=int, default=Config.SAMPLE_INTERVAL,
                        help='Seconds between checks of the frontmost window (default: %(default)s)')
    parser.add_argument('--once', action='store_true',
                        help='Capture a single snapshot and exit (useful for testing)')
    args = parser.parse_args()

    if args.once:
        wrote = poll_once(Path(Config.LOGS_DIR))
        print('[worklog] snapshot written' if wrote else '[worklog] nothing to write')
        from logger.window_info import health
        problem = health().get('problem')
        if problem:
            print(f'[worklog] WARNING: {problem}', file=sys.stderr)
    else:
        run(args.interval, Config.INACTIVITY_TIMEOUT, args.sample_interval)


if __name__ == '__main__':
    main()

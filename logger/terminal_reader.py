"""
Working directory of the frontmost terminal tab.

A terminal's window title says little about *what* you are working on, but the
cwd of the foreground process in the active tab usually points straight at a
repo. Terminal.app and iTerm2 expose the tab's tty over AppleScript; from the
tty we find the foreground process and ask lsof for its cwd. Other terminals
(Warp, Ghostty, …) have no such hook, so the caller falls back to any path
that appears in their window title.

Needs the same Automation permission the browser reader already asks for.
"""

import subprocess

from logger.extractors import parse_lsof_cwd, pick_foreground_pid

_TTY_SCRIPTS: dict[str, str] = {
    'terminal': 'tell application "Terminal" to return tty of selected tab of front window',
    'iterm2': 'tell application "iTerm2" to return tty of current session of current window',
}


def _run(cmd: list[str], timeout: float = 3) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ''


def terminal_cwd(app: str) -> str:
    """cwd of the foreground process in the frontmost tab of `app`, or ''."""
    script = _TTY_SCRIPTS.get(app.lower())
    if not script:
        return ''
    tty = _run(['osascript', '-e', script]).strip()
    if not tty.startswith('/dev/'):
        return ''
    pid = pick_foreground_pid(_run(['ps', '-t', tty.removeprefix('/dev/'), '-o', 'pid=,stat=,comm=']))
    if not pid:
        return ''
    return parse_lsof_cwd(_run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']))

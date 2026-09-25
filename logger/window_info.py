"""
Frontmost app + window title, captured through the macOS Accessibility API.

The old implementation shelled out to System Events via osascript. Without the
Accessibility permission that fails with error -1719 for the *window* lookup
while still returning the app name — and the failure was swallowed, so every
log line ended up with an empty title and nobody noticed. Here the permission
state is checked explicitly and exposed through HEALTH, so the UI can tell the
user what is wrong instead of silently logging blanks.

The permission belongs to the process that makes the calls: worklog.app when
bundled, otherwise the terminal / IDE that launched `worklog-ui`.
"""

import subprocess
import threading
from dataclasses import dataclass

try:  # pyobjc — a declared dependency, but never let a missing wheel kill the poller
    from AppKit import NSRunningApplication, NSWorkspace
    from HIServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        AXUIElementSetMessagingTimeout,
        kAXFocusedApplicationAttribute,
        kAXFocusedWindowAttribute,
        kAXMainWindowAttribute,
        kAXTitleAttribute,
        kAXTrustedCheckOptionPrompt,
    )
    _HAVE_AX = True
except ImportError:  # pragma: no cover - exercised only without pyobjc
    _HAVE_AX = False

# AX calls into an unresponsive app can otherwise block for seconds.
_AX_TIMEOUT_S = 1.0

# How many consecutive empty titles (with permission granted) before we call it a problem.
_EMPTY_STREAK_WARN = 15

_ACCESSIBILITY_PANE = 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'


@dataclass
class FrontWindow:
    app: str = ''
    window: str = ''
    pid: int = 0
    error: str = ''


_health_lock = threading.Lock()
HEALTH: dict = {
    'trusted': None,        # True / False / None (unknown: pyobjc missing)
    'empty_streak': 0,      # consecutive captures with a blank title despite trust
    'last_error': '',
}


def accessibility_trusted(prompt: bool = False) -> bool | None:
    """Is this process allowed to read window titles? `prompt` shows the system dialog."""
    if not _HAVE_AX:
        return None
    try:
        if prompt:
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def open_accessibility_settings() -> None:
    """Show the system prompt and open the Accessibility pane of System Settings."""
    accessibility_trusted(prompt=True)
    subprocess.Popen(['open', _ACCESSIBILITY_PANE])


def health() -> dict:
    """Snapshot of the capture health, with a ready-to-display `problem` message ('' if fine)."""
    with _health_lock:
        h = dict(HEALTH)
    # Ask the OS every time (cheap, no prompt): the cached value would stay wrong
    # after the user grants access while the poller is stopped.
    h['trusted'] = accessibility_trusted()
    problem = ''
    if not _HAVE_AX:
        problem = 'Window titles are unavailable: pyobjc is not installed.'
    elif h['trusted'] is False:
        problem = ('Window titles are not being captured: grant Accessibility access to '
                   'worklog (System Settings › Privacy & Security › Accessibility).')
    elif h['empty_streak'] >= _EMPTY_STREAK_WARN:
        problem = ('Window titles keep coming back empty even though access is granted. '
                   'Try removing and re-adding worklog in the Accessibility list, then restart it.')
    h['problem'] = problem
    return h


def _record(trusted, window: str, error: str) -> None:
    with _health_lock:
        HEALTH['trusted'] = trusted
        HEALTH['last_error'] = error
        if trusted and not window:
            HEALTH['empty_streak'] += 1
        else:
            HEALTH['empty_streak'] = 0


def _ax_value(element, attribute):
    try:
        err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    except Exception:
        return None
    return value if err == 0 else None


def _window_title(app_element) -> str:
    for attr in (kAXFocusedWindowAttribute, kAXMainWindowAttribute):
        win = _ax_value(app_element, attr)
        if win is not None:
            title = _ax_value(win, kAXTitleAttribute)
            if title:
                return str(title)
    return ''


def _app_name(pid: int) -> str:
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return str(app.localizedName() or '') if app else ''


def _frontmost_via_workspace() -> tuple[str, int]:
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app:
            return str(app.localizedName() or ''), int(app.processIdentifier())
    except Exception:
        pass
    return '', 0


def _frontmost_via_osascript() -> str:
    """Last resort (no pyobjc): app name only — this part works without Accessibility."""
    script = ('tell application "System Events" to name of first application process '
              'whose frontmost is true')
    try:
        r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ''


def get_front_window() -> FrontWindow:
    """Return the frontmost app and its focused window title (title '' when unreadable)."""
    if not _HAVE_AX:
        app = _frontmost_via_osascript()
        _record(None, '', 'pyobjc not available')
        return FrontWindow(app=app, error='pyobjc not available')

    trusted = bool(AXIsProcessTrusted())
    app_name, pid, title, error = '', 0, '', ''

    if trusted:
        # The system-wide element answers "who is focused *right now*", with no
        # dependence on a running Cocoa run loop (which a CLI poller lacks).
        system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, _AX_TIMEOUT_S)
        focused = _ax_value(system, kAXFocusedApplicationAttribute)
        if focused is not None:
            try:
                err, pid = AXUIElementGetPid(focused, None)
                if err != 0:
                    pid = 0
            except Exception:
                pid = 0
        if pid:
            app_name = _app_name(pid)
            app_el = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(app_el, _AX_TIMEOUT_S)
            title = _window_title(app_el)
        else:
            error = 'could not resolve the focused application'
    else:
        error = 'accessibility not granted'

    if not app_name:
        app_name, pid = _frontmost_via_workspace()
        if pid and trusted and not title:
            app_el = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(app_el, _AX_TIMEOUT_S)
            title = _window_title(app_el)
    if not app_name:
        app_name = _frontmost_via_osascript()

    _record(trusted, title, error)
    return FrontWindow(app=app_name.strip(), window=title.strip(), pid=pid, error=error)

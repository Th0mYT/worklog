import subprocess

# AppleScript templates per browser to extract active tab title + URL
_SCRIPTS: dict[str, str] = {
    'Google Chrome': '''
tell application "Google Chrome"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return t & "|||" & u
end tell''',
    'Arc': '''
tell application "Arc"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return t & "|||" & u
end tell''',
    'Safari': '''
tell application "Safari"
    set t to name of current tab of front window
    set u to URL of current tab of front window
    return t & "|||" & u
end tell''',
    'Firefox': '''
tell application "Firefox"
    set t to name of front window
    return t & "|||" & ""
end tell''',
    'Brave Browser': '''
tell application "Brave Browser"
    set t to title of active tab of front window
    set u to URL of active tab of front window
    return t & "|||" & u
end tell''',
}


def get_tab_info(browser: str) -> tuple[str, str] | None:
    """Return (title, url) of the active browser tab, or None if unavailable."""
    script = _SCRIPTS.get(browser)
    if not script:
        return None
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=5,
        )
        raw = result.stdout.strip()
        if '|||' in raw:
            title, url = raw.split('|||', 1)
            return title.strip(), url.strip()
    except Exception:
        pass
    return None

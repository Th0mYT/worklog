import subprocess
from dataclasses import dataclass


@dataclass
class TabInfo:
    title: str
    url: str
    private: bool = False


# AppleScript per browser → "title|||url|||mode". `mode` is 'incognito' for a
# private window in Chromium-based browsers (verified in their scripting
# dictionaries); Safari and Firefox expose no such property, so for those the
# domain rules are the only protection — an unclassified domain never keeps its
# title, private window or not.
_CHROMIUM_TEMPLATE = '''
tell application "{app}"
    if (count of windows) is 0 then return ""
    set w to front window
    set t to title of active tab of w
    set u to URL of active tab of w
    set m to "normal"
    try
        set m to mode of w
    end try
    return t & "|||" & u & "|||" & m
end tell'''

_SCRIPTS: dict[str, str] = {
    'Google Chrome': _CHROMIUM_TEMPLATE.format(app='Google Chrome'),
    'Arc': _CHROMIUM_TEMPLATE.format(app='Arc'),
    'Brave Browser': _CHROMIUM_TEMPLATE.format(app='Brave Browser'),
    'Safari': '''
tell application "Safari"
    if (count of windows) is 0 then return ""
    set t to name of current tab of front window
    set u to URL of current tab of front window
    return t & "|||" & u & "|||normal"
end tell''',
    'Firefox': '''
tell application "Firefox"
    if (count of windows) is 0 then return ""
    set t to name of front window
    return t & "|||" & "" & "|||normal"
end tell''',
}


def parse_tab_output(raw: str) -> TabInfo | None:
    """Parse the `title|||url|||mode` string an AppleScript above returned."""
    # Split from the right: the title is free text and may itself contain '|||',
    # which must never shift the URL/mode fields (that would hide a private window).
    parts = (raw or '').strip().rsplit('|||', 2)
    if len(parts) < 2:
        return None
    if len(parts) == 2:
        title, url, mode = parts[0], parts[1], 'normal'
    else:
        title, url, mode = parts
    return TabInfo(title=title.strip(), url=url.strip(), private=(mode.strip().lower() == 'incognito'))


def get_tab_info(browser: str) -> TabInfo | None:
    """Return the active tab of the frontmost `browser` window, or None if unavailable."""
    script = _SCRIPTS.get(browser)
    if not script:
        return None
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    return parse_tab_output(result.stdout)

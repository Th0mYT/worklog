


"""
Runtime configuration loader.

Resolution order:
  1. ~/.worklog/config.toml   (user-level, never committed)
  2. ./worklog.toml           (project-local override, gitignored)
  3. Built-in defaults

Copy worklog.example.toml → ~/.worklog/config.toml to get started.
"""

import os
import tomllib
from pathlib import Path

# ---------------------------------------------------------------------------
# App categories — add your own apps under the right category.
# An app can appear in only one category; first match wins.
#
# These are the built-in defaults. Users can override them from the Settings UI
# (persisted to the [categories] table in ~/.worklog/config.toml) — see
# Config.CATEGORIES below, which is what the poller actually reads at runtime.
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    'coding': [
        'Cursor', 'Visual Studio Code', 'WebStorm', 'PyCharm', 'IntelliJ IDEA',
        'Xcode', 'Terminal', 'iTerm2', 'Warp', 'Ghostty', 'Vim', 'Neovim',
    ],
    'meeting': [
        'Discord', 'Zoom', 'Microsoft Teams', 'FaceTime',
    ],
    'browser': [
        'Google Chrome', 'Safari', 'Firefox', 'Arc', 'Brave Browser', 'Opera',
    ],
    'design': [
        'Figma', 'Sketch', 'Adobe XD', 'Framer', 'Principle',
    ],
    'productivity': [
        'Notion', 'Obsidian', 'Bear', 'Notes', 'Craft', 'Cron', 'Fantastical',
    ],
    'communication': [
        'Slack', 'Mail', 'Spark', 'Mimestream', 'Telegram', 'WhatsApp',
    ],
}

# ---------------------------------------------------------------------------
# Browser privacy rules. Only pages on a `work` domain keep their title and
# (query-less) URL in the log and reach the summarizer; everything else is
# reduced to the bare domain — or dropped altogether for private windows.
# Subdomains match (github.com covers gist.github.com). Editable from
# Settings › Browser and persisted to the [browser_rules] table.
# ---------------------------------------------------------------------------
DEFAULT_BROWSER_RULES: dict[str, list[str]] = {
    'work': [
        'github.com', 'gitlab.com', 'bitbucket.org', 'atlassian.net', 'stackoverflow.com',
        'developer.mozilla.org', 'localhost', '127.0.0.1',
    ],
    'personal': [
        'twitch.tv', 'netflix.com', 'primevideo.com', 'facebook.com', 'instagram.com',
        'tiktok.com', 'x.com', 'twitter.com', 'fandom.com',
    ],
}

# Keywords that promote a 'meeting'-category app to type="meeting"
MEETING_WINDOW_SIGNALS: list[str] = [
    'call', 'meeting', 'standup', 'voice', 'video', 'live', 'screen share',
]

# ---------------------------------------------------------------------------
# Internal loader
# ---------------------------------------------------------------------------

def _load_toml() -> dict:
    candidates = [
        Path.home() / '.worklog' / 'config.toml',
        Path('worklog.toml'),
    ]
    for path in candidates:
        if path.exists():
            with path.open('rb') as f:
                return tomllib.load(f)
    return {}


def _normalize_categories(raw) -> dict[str, list[str]]:
    """Coerce a user-supplied [categories] table into {name: [apps]}.

    Drops blank names/apps and non-list values so a malformed config can never
    break classification. Falls back to the built-in defaults when empty.
    """
    if not isinstance(raw, dict):
        return {k: list(v) for k, v in DEFAULT_CATEGORIES.items()}
    result: dict[str, list[str]] = {}
    for name, apps in raw.items():
        name = str(name).strip()
        if not name or not isinstance(apps, list):
            continue
        cleaned = [str(a).strip() for a in apps if str(a).strip()]
        result[name] = cleaned
    return result or {k: list(v) for k, v in DEFAULT_CATEGORIES.items()}


def _normalize_commesse(raw) -> list[dict]:
    """Coerce a user-supplied [[commesse]] array into [{name, client, keywords}].

    Drops entries without a name and duplicate names. Unlike categories, an
    empty result is valid — "no commesse configured" is a normal state.
    """
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name', '')).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        client = str(item.get('client', '')).strip()
        keywords = item.get('keywords')
        keywords = [str(k).strip() for k in keywords if str(k).strip()] if isinstance(keywords, list) else []
        result.append({'name': name, 'client': client, 'keywords': keywords})
    return result


def _clean_domain(value) -> str:
    """Reduce 'https://www.Example.com/path' (or 'example.com') to 'example.com'."""
    d = str(value).strip().lower()
    if '://' in d:
        d = d.split('://', 1)[1]
    d = d.split('/', 1)[0].split('?', 1)[0].split(':', 1)[0]
    return d[4:] if d.startswith('www.') else d


def _normalize_browser_rules(raw) -> dict[str, list[str]]:
    """Coerce a user-supplied [browser_rules] table into {'work': [...], 'personal': [...]}.

    Missing/malformed sides fall back to the built-in defaults; an explicitly
    empty list is respected (the user cleared it on purpose).
    """
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, list[str]] = {}
    for side in ('work', 'personal'):
        value = raw.get(side)
        if not isinstance(value, list):
            result[side] = list(DEFAULT_BROWSER_RULES[side])
            continue
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            d = _clean_domain(item)
            if d and d not in seen:
                seen.add(d)
                cleaned.append(d)
        result[side] = cleaned
    return result


def _string_list(value, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [str(v).strip() for v in value if str(v).strip()]


_cfg = _load_toml()


class Config:
    CATEGORIES: dict[str, list[str]] = _normalize_categories(_cfg.get('categories'))
    LOGS_DIR: str           = str(Path(_cfg.get('logs_dir', '~/.worklog/logs')).expanduser())
    SUMMARIES_DIR: str      = str(Path(_cfg.get('summaries_dir', '~/.worklog/summaries')).expanduser())
    # Heartbeat: the poller writes a fresh entry at least this often, even if
    # nothing changed. Between heartbeats it only writes when the app/window changes.
    POLL_INTERVAL: int      = int(_cfg.get('poll_interval', 300))
    # How often the frontmost window is checked (seconds).
    SAMPLE_INTERVAL: int    = max(5, int(_cfg.get('sample_interval', 20)))
    INACTIVITY_TIMEOUT: int = int(_cfg.get('inactivity_timeout', 300))
    # Apps that are never logged (case-insensitive) — worklog itself by default.
    IGNORE_APPS: list[str]  = _string_list(_cfg.get('ignore_apps'), ['worklog'])
    # Window titles of these categories are never written to the log: chat
    # subjects / DM names / mail subjects have no business in a timesheet.
    REDACT_TITLE_TYPES: list[str] = _string_list(_cfg.get('redact_title_types'), ['communication'])
    BROWSER_RULES: dict[str, list[str]] = _normalize_browser_rules(_cfg.get('browser_rules'))
    # What to do with browser pages on a domain that is in neither list:
    # "hide" (default) keeps only the domain and leaves it out of the summary,
    # "work" treats it as work (title + query-less URL are logged).
    BROWSER_UNKNOWN: str    = 'work' if str(_cfg.get('browser_unknown', 'hide')).lower() == 'work' else 'hide'
    GIT_REPOS: list[str]       = _cfg.get('git_repos', [])
    GIT_WORKSPACES: list[str]  = _cfg.get('git_workspaces', [])
    GIT_REPO_TAGS: dict[str, list[str]] = _cfg.get('git_tags', {})
    GIT_AUTHOR: str            = _cfg.get('git_author', '')
    COMMESSE: list[dict]       = _normalize_commesse(_cfg.get('commesse', []))

    # Summarizer
    SUMMARIZER_BACKEND: str = _cfg.get('summarizer_backend', 'ollama')
    OLLAMA_URL: str         = _cfg.get('ollama_url', 'http://localhost:11434')
    OLLAMA_MODEL: str       = _cfg.get('ollama_model', 'qwen2.5:7b')
    COUNCIL_URL: str        = _cfg.get('council_url', 'http://localhost:8001')
    CLAUDE_MODEL: str       = _cfg.get('claude_model', '')
    ANTHROPIC_API_KEY: str  = _cfg.get('anthropic_api_key', '') or os.environ.get('ANTHROPIC_API_KEY', '')
    ANTHROPIC_MODEL: str    = _cfg.get('anthropic_model', 'claude-haiku-4-5')
    OPENAI_API_KEY: str     = _cfg.get('openai_api_key', '') or os.environ.get('OPENAI_API_KEY', '')
    OPENAI_MODEL: str       = _cfg.get('openai_model', 'gpt-4o-mini')

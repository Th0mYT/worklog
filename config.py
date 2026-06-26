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
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, list[str]] = {
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


_cfg = _load_toml()


class Config:
    LOGS_DIR: str           = str(Path(_cfg.get('logs_dir', '~/.worklog/logs')).expanduser())
    SUMMARIES_DIR: str      = str(Path(_cfg.get('summaries_dir', '~/.worklog/summaries')).expanduser())
    POLL_INTERVAL: int      = int(_cfg.get('poll_interval', 300))
    INACTIVITY_TIMEOUT: int = int(_cfg.get('inactivity_timeout', 300))
    GIT_REPOS: list[str]       = _cfg.get('git_repos', [])
    GIT_WORKSPACES: list[str]  = _cfg.get('git_workspaces', [])
    GIT_REPO_TAGS: dict[str, list[str]] = _cfg.get('git_tags', {})
    GIT_AUTHOR: str            = _cfg.get('git_author', '')

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

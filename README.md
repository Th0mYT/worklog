# worklog

Offline PC activity tracker with AI-powered daily summaries.

worklog runs quietly in the background, capturing what app and window is active every few minutes and enriching it with git commit history from your repos. At the end of the day, an LLM turns that raw log into a clean timesheet you can paste straight into your tracker.

No data leaves your machine unless you choose a cloud backend (OpenAI).

---

## How it works

```
┌─────────────────────┐     every 5 min      ┌──────────────────────────┐
│  activity_poller    │ ──────────────────▶  │  ~/.worklog/logs/        │
│  (via UI or CLI)    │                       │  YYYY-MM-DD.jsonl        │
└─────────────────────┘                       │                          │
                                              │  { ts, app, window,     │
┌─────────────────────┐     on demand         │    type, url… }          │
│  git_enricher       │ ──────────────────▶  │  { source: git, repo,   │
│  (manual / UI)      │                       │    message, stats… }     │
└─────────────────────┘                       └────────────┬─────────────┘
                                                           │
                                              ┌────────────▼─────────────┐
                                              │  daily_summary           │
                                              │  Ollama · Claude · OpenAI│
                                              │  · llm-council           │
                                              └────────────┬─────────────┘
                                                           │
                                              ┌────────────▼─────────────┐
                                              │  Timesheet-ready output  │
                                              │  one line per session    │
                                              └──────────────────────────┘
```

---

## Requirements

- macOS (activity poller uses AppleScript)
- Python 3.11+
- At least one summarizer backend (pick one):
  - **Ollama** (default, fully local) — `ollama pull qwen2.5:7b`
  - **Claude CLI** — `npm install -g @anthropic-ai/claude-code` + auth
  - **OpenAI** — API key in config or `OPENAI_API_KEY` env var
  - **llm-council** — [llm-council](https://github.com/Th0mYT/llm-council), needs beefy hardware

---

## Installation

```bash
git clone https://github.com/Th0mYT/worklog
cd worklog
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

---

## Configuration

Copy the example config to your home directory and edit it:

```bash
mkdir -p ~/.worklog
cp worklog.example.toml ~/.worklog/config.toml
```

```toml
# ~/.worklog/config.toml

logs_dir          = "~/.worklog/logs"
poll_interval     = 300          # seconds between snapshots
inactivity_timeout = 300         # pause poller after this many seconds idle
git_author        = "yourname"   # substring matched against git author name or email

# Repos to scan for commits (explicit list)
git_repos = [
    "/Users/you/projects/repo-one",
]

# Or point at a workspace folder — all git repos inside are discovered automatically
git_workspaces = [
    "/Users/you/projects",
]

# ── Summarizer backend ────────────────────────────────────────────────────────
# "ollama" (default) | "claude" | "openai" | "council"

summarizer_backend = "ollama"
ollama_url         = "http://localhost:11434"
ollama_model       = "qwen2.5:7b"

# claude_model     = ""                        # leave empty for default model
# openai_api_key   = "sk-…"                   # or set OPENAI_API_KEY in env
# openai_model     = "gpt-4o-mini"
# council_url      = "http://localhost:8001"
```

---

## Usage

### UI (recommended)

```bash
python -m ui.app
```

A native macOS window lets you:

- **Start / Stop** the activity poller
- **Enrich** — pull today's git commits into the log
- **Generate Summary** — run the enricher then call the LLM; a cancel button lets you abort mid-request
- **Browse logs** — view any day's entries, delete individual entries, reset or wipe all logs
- **Settings** — configure repos, backends, API keys, and idle timeout; changes are saved to `~/.worklog/config.toml`

> **macOS permission:** the activity poller uses AppleScript via System Events to read the frontmost app and window title. macOS will prompt for **Accessibility** access the first time (System Settings → Privacy & Security → Accessibility). No keystrokes or mouse data are recorded.

### CLI

**Activity poller**

```bash
python -m logger.activity_poller --once    # single snapshot
python -m logger.activity_poller           # continuous loop
```

**Git enricher**

```bash
python -m logger.git_enricher                    # today
python -m logger.git_enricher --date 2026-06-17  # specific date
python -m logger.git_enricher --dry-run          # preview without writing
```

**Daily summary**

```bash
python -m summarizer.daily_summary                    # today
python -m summarizer.daily_summary --date 2026-06-17  # specific date
python -m summarizer.daily_summary --print-prompt     # debug: show the prompt
python -m summarizer.daily_summary --backend claude   # override backend
```

Example output (with project tags):

```
## #my-api

Timezone-aware scheduling support (0.5h) [my-api]
Analytics module refactoring — metric handling, filtering, query DTO consolidation (3.0h) [my-api]
Session event & status logic — skip support, status sync, progress calculation (2.0h) [my-api]
Redis logging fix — deduplicated connection events (0.5h) [my-api]

## General

Browser research — documentation and PR reviews (1.0h)

Total: 7.5h
```

---

## Log format

Each line in the daily `.jsonl` file is one of:

**Activity snapshot** (from poller):
```json
{"ts": "2026-06-18T09:05:00", "app": "Cursor", "window": "worklog – ui/app.py", "type": "coding"}
{"ts": "2026-06-18T10:30:00", "app": "Google Chrome", "window": "", "type": "browser", "tab_title": "GitHub PR #42", "url": "https://github.com/..."}
{"ts": "2026-06-18T11:00:00", "app": "Discord", "window": "standup call", "type": "meeting"}
```

**Git commit** (from enricher):
```json
{"ts": "2026-06-18T11:45:00+02:00", "source": "git", "repo": "my-api", "type": "coding", "commit": "a1b2c3d4", "message": "feat: add user auth", "files_changed": 5, "insertions": 120, "deletions": 30}
```

**Project tags** — add optional tags to any repo in the config; they appear in each commit entry and the summarizer groups output by tag:

```toml
[git_tags]
"/Users/you/projects/my-api" = ["my-api", "backend"]
```

---

## App categories

Edit `config.py` to add apps to the right category. The poller uses these to tag each snapshot:

| Category | Examples |
|---|---|
| `coding` | Cursor, VS Code, WebStorm, Terminal |
| `meeting` | Discord, Zoom, Teams |
| `browser` | Chrome, Safari, Arc, Firefox |
| `design` | Figma, Sketch |
| `communication` | Slack, Mail, Telegram |
| `productivity` | Notion, Obsidian |

---

## Summarizer backends

| Backend | Config value | Notes |
|---|---|---|
| Ollama | `ollama` | Default. Fully local, no API key needed. |
| Claude CLI | `claude` | Requires `claude` on PATH. Uses your existing Claude auth. |
| OpenAI | `openai` | Requires `openai_api_key` in config or `OPENAI_API_KEY` env var. |
| llm-council | `council` | Multi-model synthesis. Needs a running [llm-council](https://github.com/Th0mYT/llm-council) instance. |

---

## License

MIT

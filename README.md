# worklog

**Offline-first macOS activity tracker with AI-powered daily summaries.**

worklog runs quietly in the background, capturing what app and window is active every few
minutes and enriching that log with git commit history from your repos. At the end of the
day, an LLM turns the raw log into a clean, client-ready timesheet you can paste straight
into your time-tracking tool.

No data leaves your machine unless you choose a cloud backend (Anthropic, OpenAI, or a
remote llm-council instance).

---

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [UI](#ui-recommended)
  - [CLI](#cli)
- [Log format](#log-format)
- [App categories](#app-categories)
- [Client work orders (commesse)](#client-work-orders-commesse)
- [Summarizer backends](#summarizer-backends)
- [macOS permissions](#macos-permissions)
- [Building the app](#building-the-app)
- [License](#license)

---

## Features

- **Passive activity capture** — watches the frontmost app and window title, writing an entry
  when something changes (plus a heartbeat), so short switches aren't lost. IDE titles are
  parsed into project + file, terminals are resolved to their repo through the tab's working
  directory, and the checked-out branch and uncommitted files are recorded for configured
  repos.
- **Privacy by design** — private/incognito windows are never recorded, browser pages keep
  their title only on domains you mark as *work* (query strings are always dropped), chat
  window titles are never stored, and anything personal or unclassified is left out of the
  summary *and* never sent to the LLM. A one-click **Pause** (⌘⇧P) covers everything rules
  can't, and any entry can be marked personal afterwards. See
  [Privacy](#privacy-what-is-and-isnt-recorded).
- **Git enrichment** — pulls the day's commits (by author, across explicit repos or whole
  workspace folders) into the same log, tagged per project.
- **Manual time blocks** — backfill a gap the poller missed, or log time by hand as a point
  event or a start/end range, with an optional free-text note passed straight into the
  summarizer prompt.
- **Editable project tags** — the poller and git enricher tag entries automatically when a
  configured repo name shows up in the window title, but that match isn't always there (an
  IDE window title that doesn't include the repo folder name, a repo not yet in your
  config…). From the log detail view you can add or fix the project tag(s) on any entry by
  hand, so it's grouped under the right commessa/category in the summary instead of landing
  in "Fuori commessa" and needing a manual fix afterward.
- **AI daily summaries** — five interchangeable backends (Ollama, Claude CLI, Anthropic,
  OpenAI, llm-council), with live progress feedback and a cancel button. Durations, gaps,
  commessa assignment and the total are computed in code; the model only writes one short
  description per block, so a small local model can't skew the numbers.
- **Client work orders (commesse)** — group a day's sessions by client/project based on
  git repo, tag, or keyword matches, entirely configurable from the UI.
- **Configurable categories** — classify apps into `coding`, `meeting`, `browser`, `design`,
  `productivity`, `communication`, or your own custom set.
- **Native macOS UI** — start/stop the poller, enrich, summarize, browse and edit any day's
  log, and manage settings, all from a lightweight `pywebview` window. Summaries are saved
  to disk and reloaded automatically when you revisit a date.
- **Fully offline by default** — the default backend (Ollama) and all activity capture run
  locally; nothing is sent anywhere unless you opt into a cloud backend.

---

## How it works

```
┌─────────────────────┐   on change + beat   ┌──────────────────────────┐
│  activity_poller    │ ──────────────────▶  │  ~/.worklog/logs/        │
│  (via UI or CLI)    │                       │  YYYY-MM-DD.jsonl        │
└─────────────────────┘                       │                          │
                                               │  { ts, app, window,     │
┌─────────────────────┐     on demand         │    type, url… }          │
│  git_enricher       │ ──────────────────▶  │  { source: git, repo,   │
│  (manual / UI)      │                       │    message, stats… }     │
└─────────────────────┘                       │                          │
                                               │  { manual: true,        │
┌─────────────────────┐     manual entry      │    note, end_ts… }       │
│  UI "add block"     │ ──────────────────▶  └────────────┬─────────────┘
└─────────────────────┘                                    │
                                              ┌─────────────▼─────────────┐
                                              │  daily_summary            │
                                              │  Ollama · Claude CLI      │
                                              │  Anthropic · OpenAI       │
                                              │  · llm-council            │
                                              │  (grouped by commessa,    │
                                              │   if configured)          │
                                              └─────────────┬─────────────┘
                                                             │
                                              ┌─────────────▼─────────────┐
                                              │  ~/.worklog/summaries/    │
                                              │  YYYY-MM-DD.md            │
                                              │  timesheet-ready output   │
                                              └────────────────────────────┘
```

---

## Requirements

- macOS (the activity poller and browser-tab reader use AppleScript / CoreGraphics)
- Python 3.11+
- At least one summarizer backend (pick one):
  - **Ollama** (default, fully local) — `ollama pull qwen2.5:7b`
  - **Claude CLI** — `npm install -g @anthropic-ai/claude-code` + auth
  - **Anthropic** — API key in config or `ANTHROPIC_API_KEY` env var
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

This installs two console scripts into the venv: `worklog-ui` and `worklog-poll`.

---

## Configuration

Copy the example config to your home directory and edit it:

```bash
mkdir -p ~/.worklog
cp worklog.example.toml ~/.worklog/config.toml
```

Config is resolved in this order: `~/.worklog/config.toml` (user-level) →
`./worklog.toml` (project-local, gitignored) → built-in defaults. Most of it can also be
edited from the UI's **Settings** screen, which writes back to
`~/.worklog/config.toml`.

```toml
# ~/.worklog/config.toml

logs_dir           = "~/.worklog/logs"       # where daily .jsonl logs are stored
summaries_dir       = "~/.worklog/summaries" # where generated {date}.md summaries are saved
sample_interval     = 20           # seconds between checks of the frontmost window
poll_interval       = 300          # heartbeat: write at least this often even if nothing changed
inactivity_timeout  = 300          # pause poller after this many seconds idle
ignore_apps         = ["worklog"]  # never logged
redact_title_types  = ["communication"]  # categories whose window titles are never stored
browser_unknown     = "hide"       # sites in neither list below: "hide" (leave out) or "work"
git_author          = "yourname"   # substring matched against git author name or email

# Repos to scan for commits (explicit list)
git_repos = [
    "/Users/you/projects/repo-one",
]

# Or point at a workspace folder — all git repos inside are discovered automatically
git_workspaces = [
    "/Users/you/projects",
]

# Optional per-repo tags, surfaced on each commit entry and used by the
# summarizer to group output — see "App categories" below.
[git_tags]
"/Users/you/projects/repo-one" = ["repo-one", "backend"]

# ── Summarizer backend ──────────────────────────────────────────────────────
# "ollama" (default) | "claude" | "anthropic" | "openai" | "council"

summarizer_backend = "ollama"
ollama_url          = "http://localhost:11434"
ollama_model        = "qwen2.5:7b"

# claude_model       = ""                      # leave empty for default model
# anthropic_api_key  = "sk-ant-…"               # or set ANTHROPIC_API_KEY in env
# anthropic_model    = "claude-haiku-4-5"
# openai_api_key     = "sk-…"                   # or set OPENAI_API_KEY in env
# openai_model       = "gpt-4o-mini"
# council_url        = "http://localhost:8001"

# Optional: which websites count as work (see "Privacy"); defaults are built in
# [browser_rules]
# work     = ["github.com", "atlassian.net", "localhost"]
# personal = ["twitch.tv", "netflix.com"]

# Optional: override the built-in app → category mapping (see "App categories")
# [categories]
# coding = ["Cursor", "Visual Studio Code", "Terminal"]

# Optional: client work orders for grouping the daily summary (see "Client
# work orders" below)
# [[commesse]]
# name     = "Backend revamp"
# client   = "Acme Corp"
# keywords = ["acme", "backend-api"]
```

See [`worklog.example.toml`](worklog.example.toml) for the full, commented reference.

---

## Usage

### UI (recommended)

```bash
worklog-ui
# or, without installing the console script:
python -m ui.app
```

A native macOS window lets you:

- **Start / Stop** the activity poller, or **Pause** it (for 15 min, 30 min, 1 h or until
  you resume; ⌘⇧P toggles) — nothing is recorded while paused
- see a **warning banner** if window titles can't be read (missing Accessibility permission)
  with a button that opens the right System Settings pane
- **Enrich** — pull today's git commits into the log
- **Generate Summary** — run the enricher then call the LLM, with live progress output,
  backend-specific hints, and a cancel button to abort mid-request
- **Browse logs** — view any day's entries, add a manual time block or note, delete
  individual entries, mark an entry as personal (🚫, reversible), classify a site as work or
  personal, reset today's log or wipe all logs
- **Settings** — configure repos/workspaces, git tags, app categories, commesse, browser
  rules, ignored apps, backends, API keys, sampling and idle timeout; changes are saved to
  `~/.worklog/config.toml`

> **macOS permissions:** see [macOS permissions](#macos-permissions) below.

### CLI

**Activity poller**

```bash
worklog-poll --once      # single snapshot
worklog-poll             # continuous loop
worklog-poll --sample-interval 10 --interval 600   # check every 10 s, heartbeat every 10 min
# equivalently:
python -m logger.activity_poller --once
python -m logger.activity_poller
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
python -m summarizer.daily_summary --no-llm           # no model: descriptions built from the data, nothing saved
python -m summarizer.daily_summary --backend claude   # override backend
```

The command also reports what it left out — private browsing, personal or unclassified
sites, blocks shorter than 5 minutes — and lists the sites still waiting for a work/personal
classification.

**Clean up old logs**

Logs written before the privacy rules existed may hold full URLs and page titles. Apply the
current rules to them (a `.bak` copy of every changed file is kept; the default is a dry run):

```bash
python -m logger.redact_logs            # report what would change
python -m logger.redact_logs --apply
```

**Tests**

```bash
python -m unittest discover -s tests -t .
```

Generated summaries are written to `~/.worklog/summaries/{date}.md` and reused by the UI
the next time you open that date.

Example output (with commesse configured):

```
## Backend revamp

Timezone-aware scheduling support (0.5h) [backend-api]
Analytics module refactoring — metric handling, filtering, query DTO consolidation (3.0h) [backend-api]
Session event & status logic — skip support, status sync, progress calculation (2.0h) [backend-api]
Redis logging fix — deduplicated connection events (0.5h) [backend-api]

## Fuori commessa

Team communication and PR reviews (1.0h) [Slack]

Total: 7.5h
```

---

## Log format

Each line in the daily `.jsonl` file is one of:

**Activity entry** (from poller) — "at `ts`, this was in front"; how long it lasted is derived
from the next entry, so nothing needs a fixed polling grid:
```json
{"ts": "2026-06-18T09:05:00", "app": "Cursor", "window": "ui/app.py — worklog", "type": "coding", "project": "worklog", "file": "app.py", "branch": "feature/KH-682-fix", "tags": ["personal"]}
{"ts": "2026-06-18T10:30:00", "app": "Google Chrome", "window": "", "type": "browser", "domain": "github.com", "site_class": "work", "tab_title": "GitHub PR #42", "url": "https://github.com/o/r/pull/42"}
{"ts": "2026-06-18T10:45:00", "app": "Google Chrome", "window": "", "type": "browser", "domain": "twitch.tv", "site_class": "personal"}
{"ts": "2026-06-18T10:50:00", "app": "Google Chrome", "window": "", "type": "browser", "excluded": true, "reason": "private"}
{"ts": "2026-06-18T11:00:00", "app": "Discord", "window": "standup call", "type": "meeting"}
```
`wip_files` (uncommitted files) appears on heartbeat entries for configured repos.

**Marker** — delimits time you weren't there; the summarizer trims and drops entries around it:
```json
{"ts": "2026-06-18T12:02:10", "marker": "idle_start"}
```
(`idle_start`/`idle_end`, `pause`/`resume`, `stop`/`start`.)

`excluded: true` on any entry — set automatically for private windows and ignored apps, or by
you with the 🚫 button — keeps it out of every summary.

**Git commit** (from enricher):
```json
{"ts": "2026-06-18T11:45:00", "source": "git", "repo": "my-api", "type": "coding", "commit": "a1b2c3d4", "branch": "feature/KH-682-auth", "message": "feat: add user auth", "files_changed": 5, "insertions": 120, "deletions": 30, "files": ["src/auth.ts"]}
```

**Manual entry** (added from the UI, or by hand):
```json
{"ts": "2026-06-18T14:00:00", "type": "coding", "manual": true, "end_ts": "2026-06-18T15:30:00", "note": "client call prep"}
```
`end_ts` and `note` are both optional — omit `end_ts` for a point-in-time entry, omit `note`
to let the summarizer describe it from context alone.

Commits are read from every local branch and timestamps are stored as local time without an
offset, so they sort correctly next to poller entries.

**Project tags** — add optional tags to any repo in the config; they appear in each commit
and activity entry (when the IDE project, the terminal's repo or the window title matches the
repo name) and the summarizer groups output by tag:

```toml
[git_tags]
"/Users/you/projects/my-api" = ["my-api", "backend"]
```

When the automatic window-title match misses an entry — an IDE title that doesn't include
the repo folder name, or a repo you haven't tagged yet — open that day in the UI, click the
🏷 button on the entry, and add the tag(s) by hand. It's saved straight into the entry's
`tags` field, so it's matched by the commessa/tag grouping rules exactly like an automatic
tag.

---

## App categories

Each app is classified into a category — first match wins. The built-in defaults live in
`config.py`; override them from **Settings › Categories** in the UI (persisted to the
`[categories]` table in `~/.worklog/config.toml`) or by editing the table directly.

| Category | Examples |
|---|---|
| `coding` | Cursor, VS Code, WebStorm, Terminal |
| `meeting` | Discord, Zoom, Teams |
| `browser` | Chrome, Safari, Arc, Firefox |
| `design` | Figma, Sketch |
| `communication` | Slack, Mail, Telegram |
| `productivity` | Notion, Obsidian |

A `meeting`-category app is further tagged `type: "meeting"` when its window title contains
a signal word (`call`, `standup`, `voice`, `video`, …) — see `MEETING_WINDOW_SIGNALS` in
`config.py`.

---

## Client work orders (commesse)

If you bill or report time by client or project, define a `[[commesse]]` entry per work
order in your config (or from **Settings › Commesse** in the UI):

```toml
[[commesse]]
name     = "Backend revamp"
client   = "Acme Corp"
keywords = ["acme", "backend-api"]

[[commesse]]
name     = "Redesign sito"
client   = "Beta srl"
keywords = ["beta", "figma"]
```

Assignment is rule-based, not up to the model. A commessa matches when one of its `keywords`
appears as a whole token in the repo name, branch, IDE project/file, window or page title,
tag, note or commit message (`KH-682` matches `feature/KH-682-fix` but not `KH-6820`); the
client name counts as a keyword only when a single commessa has that client. Unassigned
work in a repo also takes the commessa of that repo's next matching commit (within 2 hours).
Everything unmatched goes under `## Fuori commessa`. Put a ticket key or repo name in
`keywords` and both the IDE time and the commits will land in the right place. Omit the
table entirely if you don't track work by client.

---

## Summarizer backends

| Backend | Config value | Notes |
|---|---|---|
| Ollama | `ollama` | Default. Fully local, no API key needed. |
| Claude CLI | `claude` | Requires `claude` on PATH. Uses your existing Claude auth. |
| Anthropic | `anthropic` | Direct Messages API. Requires `anthropic_api_key` in config or `ANTHROPIC_API_KEY` env var. Defaults to `claude-haiku-4-5`. |
| OpenAI | `openai` | Requires `openai_api_key` in config or `OPENAI_API_KEY` env var. |
| llm-council | `council` | Multi-model synthesis. Needs a running [llm-council](https://github.com/Th0mYT/llm-council) instance. |

---

## Privacy: what is and isn't recorded

| What | Recorded? |
|---|---|
| Keystrokes, mouse content, screen | Never. Only *whether* there was recent input (idle detection). |
| Private / incognito window (Chrome, Brave, Arc) | Nothing — not even the domain. Just `excluded: true`. |
| Page on a `work` domain | Title and URL **without** query string / fragment. |
| Page on a `personal` domain, or an unclassified one | The domain only, and it is left out of the summary. |
| Chat / mail window titles (`communication` category) | Never — only the app name. |
| Apps in `ignore_apps` (worklog itself by default) | Time is excluded, no title. |
| While paused | Nothing at all. |

Everything left out is also withheld from the prompt, so it never reaches a cloud backend.
`--print-prompt` (or the **Prompt** button) shows exactly what would be sent.

Things to know:

- **Safari and Firefox** expose no "private window" flag to scripts, so for them the domain
  rules are the only protection: an unclassified domain never keeps its title, private
  window or not.
- A video on a domain you marked *work* (say YouTube, for tutorials) can't be told apart from
  a personal one. Use **Pause** (⌘⇧P) before, or 🚫 on the entries afterwards.
- Sites in neither list are left out until you classify them: **Settings › Browser & privacy**
  lists the ones seen recently, and the log view has `→ work` / `→ personal` buttons.
  Classifying a site applies to past entries too (the summarizer judges by today's rules).
  Set `browser_unknown = "work"` to count unknown sites as work instead.
- The ⌘⇧P shortcut works while the worklog window is focused; there is no global hotkey (it
  would need yet another permission).

---

## macOS permissions

- **Accessibility** — needed to read the title of the window in front (project, file,
  meeting name). Without it the poller still records the app name but every window title is
  empty, and the UI shows a warning banner. The permission belongs to the process that runs
  the poller: `worklog.app` when bundled, otherwise the terminal or IDE you launched
  `worklog-ui` from (System Settings → Privacy & Security → Accessibility). No keystrokes or
  mouse content are ever recorded — idle time comes from CoreGraphics, which needs no
  permission at all.
- **Automation** — reading the active tab title/URL from a supported browser (Chrome, Arc,
  Safari, Firefox, Brave), and the working directory of a Terminal/iTerm2 tab, triggers a
  one-time "worklog wants to control \<App\>" prompt the first time each one is polled while
  frontmost.

---

## Building the app

`build_dmg.sh` packages worklog into a standalone `worklog.app` (via `py2app`) and wraps it
in a distributable DMG. Run it from the project root with the venv from
[Installation](#installation) already set up:

```bash
bash build_dmg.sh
```

This script:

1. Installs build-only dependencies (`py2app`, `pyobjc*`) into the venv.
2. Cleans any previous `build/`/`dist/` output.
3. Regenerates the app icon (`make_icon.py` → `assets/worklog.icns`).
4. Runs `setup.py py2app` to produce `dist/worklog.app`.
5. Bundles any native `.dylib`s py2app misses so the app runs standalone (looked up in the
   Python install's own `lib/`, then conda, Homebrew and `/usr/local`).
6. Code-signs the app (see [Code signing](#code-signing) below).
7. Packs `dist/worklog.app` into `dist/worklog-<version>.dmg`.

Output: `dist/worklog.app` and `dist/worklog-<version>.dmg` (version is set by `VERSION` at
the top of `build_dmg.sh`). Both are git-ignored.

### Code signing

macOS ties the Accessibility and Automation grants to the app's code identity. By default the
script signs ad-hoc, and an ad-hoc signature changes with every build, so after each rebuild
the grant stops matching and window titles go blank until you re-grant it. To keep the grant
across rebuilds, sign with a stable identity:

```bash
security find-identity -v -p codesigning          # list the identities in your keychain
CODESIGN_IDENTITY="Apple Development: Your Name (XXXXXXXXXX)" bash build_dmg.sh
```

Any certificate from your keychain works (a free Apple Development one is enough for local
use), as long as you use the same one every time. The identity can be the name or the SHA-1
hash shown by `find-identity`. Builds signed this way aren't notarized: fine for your own
machine, but other people will need to right-click → Open the first time.

### Installing your build

To replace an installed copy with a fresh build, quit worklog first, then:

```bash
rm -rf /Applications/worklog.app
cp -R dist/worklog.app /Applications/worklog.app
codesign --verify --deep --strict /Applications/worklog.app && echo OK
```

Or open `dist/worklog-<version>.dmg` and drag the app into `/Applications`.

### Notes

- macOS + Xcode Command Line Tools required (`xcode-select --install`) — py2app and PyObjC
  need them.
- To build without the DMG step, run `python setup.py py2app` directly after
  `make_icon.py`; the result is still `dist/worklog.app` (unsigned and without the extra
  dylibs, so prefer the script for anything you'll actually run).
- `PYTHON=/path/to/python bash build_dmg.sh` overrides which interpreter builds the app
  (defaults to `.venv/bin/python`).
- The script temporarily renames `pyproject.toml` while py2app runs (py2app rejects the
  `install_requires` setuptools would otherwise inject) and restores it on exit. If a build is
  killed hard, check that `pyproject.toml` is back and not left as `pyproject.toml.bak`.

---

## License

[GPLv3](https://github.com/Th0mYT/worklog/tree/main?tab=GPL-3.0-1-ov-file)

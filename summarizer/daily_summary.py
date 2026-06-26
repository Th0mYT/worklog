"""
Daily summarizer — reads the JSONL log for a given date and produces
a timesheet-ready summary via a local or cloud LLM.

Backends (set summarizer_backend in config.toml):
  ollama   — single Ollama model, works on any modern laptop  [default]
  council  — llm-council multi-model, needs beefy hardware
  claude   — Claude CLI (claude --print), uses your existing claude auth
  anthropic — Anthropic Messages API (requires ANTHROPIC_API_KEY)
  openai   — OpenAI chat completions API (requires OPENAI_API_KEY)

Usage:
  python -m summarizer.daily_summary                    # summarize today
  python -m summarizer.daily_summary --date 2026-06-17
  python -m summarizer.daily_summary --date 2026-06-17 --print-prompt
  python -m summarizer.daily_summary --backend openai   # override config
"""

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from config import Config

# ---------------------------------------------------------------------------
# Log loading
# ---------------------------------------------------------------------------

def _load_entries(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    entries = []
    with log_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return sorted(entries, key=lambda e: e.get('ts', ''))


# ---------------------------------------------------------------------------
# Prompt building (shared by both backends)
# ---------------------------------------------------------------------------

def _tag_suffix(e: dict) -> str:
    tags = e.get('tags') or []
    return ('  [' + ', '.join(f'#{t}' for t in tags) + ']') if tags else ''


def _format_entries(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        ts = e.get('ts', '')[:16]
        suffix = _tag_suffix(e)
        if e.get('source') == 'git':
            stats = f"+{e.get('insertions', 0)}/-{e.get('deletions', 0)} in {e.get('files_changed', 0)} file(s)"
            lines.append(f"{ts}  [git/{e.get('repo', '?')}]  {e.get('message', '')}  ({stats}){suffix}")
        else:
            activity_type = e.get('type', 'other')
            app = e.get('app', '')
            window = e.get('tab_title') or e.get('window') or ''
            label = f"{app} — {window}" if window else app
            lines.append(f"{ts}  [{activity_type}]  {label}{suffix}")
    return '\n'.join(lines)


def build_prompt(target: date, entries: list[dict]) -> str:
    has_activity = any(e.get('source') != 'git' for e in entries)
    has_git      = any(e.get('source') == 'git' for e in entries)
    has_tags     = any(e.get('tags') for e in entries)

    notes = []
    if not has_activity:
        notes.append('Note: only git commits are available — the activity poller was not running.')
    if not has_git:
        notes.append('Note: no git commits found — git enricher was not run.')
    note_block = ('\n' + '\n'.join(notes) + '\n') if notes else ''

    tag_rules = (
        '8. Some entries end with project tags like [#ddh, #backend]. These are USER-DEFINED\n'
        '   PROJECT TAGS — completely different from the activity-type labels like [coding] or\n'
        '   [other] that appear in position 2 of each line. Do NOT treat activity types as tags.\n'
        '   Group sessions by their project tag under a "## #tag-name" header.\n'
        '   Untagged sessions go under "## General" (omit "## General" if all sessions are tagged).\n'
        '9. End with a blank line then:\n'
        '   Total: Xh'
    ) if has_tags else (
        '8. End with a blank line then:\n'
        '   Total: Xh'
    )

    return f"""You are analyzing a PC activity log to produce a timesheet summary for {target}.
{note_block}
Log entry format:
  TIMESTAMP  [activity-type]  app/description  (stats if git)  [#project-tags if any]

Activity-type labels (position 2, in brackets) — these classify the app, they are NOT tags:
  [git/repo]       committed change — has timestamp, message, line stats
  [coding]         active coding session snapshot (every ~5 min)
  [meeting]        active call / video meeting snapshot
  [browser]        browser tab — title and URL when available
  [design]         design tool (Figma, Sketch…)
  [communication]  chat app (Slack, Mail…) — not a live call
  [other]          unclassified app

Project tags (optional, at the END of a line, prefixed with #) identify the project/client.
Example line with tags: 2026-06-18T17:39  [git/my-api]  feat: add endpoint (+1/-0 in 1 file(s))  [#acme, #backend]

ACTIVITY LOG:
{_format_entries(entries)}

TASK:
Produce a concise timesheet for this day. Rules:
1. Group related activities into sessions. A gap > 30 min = new session or break.
2. Estimate each session duration from the first and last timestamp in the group.
3. Coding sessions: describe the work using commit messages, not raw text.
4. Meeting sessions: name the meeting from the window title if visible.
5. Browser sessions: group by topic (e.g. "research on X", "PR review").
6. Skip sessions under 5 min unless they contain a git commit.
7. Output ONE LINE PER SESSION in this exact format:
   Description (Xh) [repo or tool reference if relevant]
{tag_rules}

No prose. No explanations. Only the session lines (and headers if project tags exist)."""


# ---------------------------------------------------------------------------
# Backend: Ollama (single model — default, works on any laptop)
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, base_url: str) -> str:
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False,
    }
    body = json.dumps(payload).encode()
    url = f'{base_url.rstrip("/")}/api/chat'
    req = urllib.request.Request(
        url, data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            content = data.get('message', {}).get('content', '')
            if not content:
                raise RuntimeError(
                    f'Ollama returned an empty response.\n'
                    f'Full payload: {json.dumps(data)[:400]}'
                )
            return content.strip()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')[:400]
        raise RuntimeError(
            f'Ollama HTTP {e.code} at {url}\n{body_text}'
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f'Cannot reach Ollama at {base_url}\n'
            f'Check that Ollama is running:  ollama serve\n'
            f'Check that model is pulled:    ollama pull {model}\n'
            f'Error: {e}'
        ) from e
    except (json.JSONDecodeError, KeyError) as e:
        raise RuntimeError(f'Unexpected Ollama response format: {e}') from e


# ---------------------------------------------------------------------------
# Backend: llm-council (multi-model — needs beefy hardware)
# ---------------------------------------------------------------------------

def _post(url: str, payload: dict, timeout: int = 300) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _call_council(prompt: str, base_url: str) -> str:
    conv = _post(f'{base_url}/api/conversations', {'title': 'worklog summary'})
    print('[worklog] Council is processing... (local models may take 1-2 min)', flush=True)
    result = _post(f'{base_url}/api/conversations/{conv["id"]}/message', {'content': prompt})
    stage3 = result.get('stage3') or {}
    return (stage3.get('response') or result.get('content') or '').strip()


# ---------------------------------------------------------------------------
# Backend: OpenAI chat completions
# ---------------------------------------------------------------------------

def _call_openai(prompt: str, model: str, api_key: str) -> str:
    if not api_key:
        raise RuntimeError(
            'OpenAI API key not set.\n'
            'Add openai_api_key to ~/.worklog/config.toml or set OPENAI_API_KEY in env.'
        )
    payload = {
        'model': model or 'gpt-4o-mini',
        'messages': [{'role': 'user', 'content': prompt}],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            content = data['choices'][0]['message']['content']
            if not content:
                raise RuntimeError('OpenAI returned an empty response')
            return content.strip()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')[:400]
        raise RuntimeError(f'OpenAI HTTP {e.code}\n{body_text}') from e
    except urllib.error.URLError as e:
        raise RuntimeError(f'Cannot reach OpenAI API: {e}') from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f'Unexpected OpenAI response format: {e}') from e


# ---------------------------------------------------------------------------
# Backend: Anthropic Messages API  (direct, requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

def _call_anthropic(prompt: str, model: str, api_key: str) -> str:
    if not api_key:
        raise RuntimeError(
            'Anthropic API key not set.\n'
            'Add anthropic_api_key to ~/.worklog/config.toml or set ANTHROPIC_API_KEY in env.'
        )
    payload = {
        'model': model or 'claude-haiku-4-5',
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': prompt}],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            # content is a list of blocks; take the first text block
            content = next(
                (b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text'),
                '',
            )
            if not content:
                raise RuntimeError('Anthropic returned an empty response')
            return content.strip()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='replace')[:400]
        raise RuntimeError(f'Anthropic HTTP {e.code}\n{body_text}') from e
    except urllib.error.URLError as e:
        raise RuntimeError(f'Cannot reach Anthropic API: {e}') from e
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f'Unexpected Anthropic response format: {e}') from e


# ---------------------------------------------------------------------------
# Backend: Claude CLI  (claude --print, uses existing claude auth)
# ---------------------------------------------------------------------------

def _call_claude(prompt: str, model: str = '') -> str:
    claude_bin = shutil.which('claude')
    if not claude_bin:
        raise RuntimeError(
            'claude CLI not found on PATH.\n'
            'Install it from https://claude.ai/code and make sure it is on your PATH.'
        )
    cmd = [claude_bin, '--print']
    if model:
        cmd += ['--model', model]
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f'claude CLI exited with code {result.returncode}\n'
                f'{(result.stderr or result.stdout).strip()}'
            )
        output = result.stdout.strip()
        if not output:
            raise RuntimeError('claude CLI returned empty output')
        return output
    except subprocess.TimeoutExpired:
        raise RuntimeError('claude CLI timed out after 300 s')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def summarize(
    target: date | None = None,
    log_dir: Path | None = None,
    backend: str | None = None,
    print_prompt: bool = False,
) -> bool:
    target  = target  or date.today()
    log_dir = log_dir or Path(Config.LOGS_DIR)
    backend = backend or Config.SUMMARIZER_BACKEND

    log_file = log_dir / f'{target}.jsonl'

    print(f'Date:      {target}')
    print(f'Log file:  {log_file}')

    entries = _load_entries(log_file)

    if not entries:
        print(f'ERROR: no entries found for {target}', file=sys.stderr)
        print(f'Tip:  start the poller, then run: python -m logger.git_enricher --date {target}', file=sys.stderr)
        return False

    git_count      = sum(1 for e in entries if e.get('source') == 'git')
    activity_count = len(entries) - git_count
    print(f'Entries:   {activity_count} activity snapshot(s), {git_count} git commit(s)')

    prompt = build_prompt(target, entries)

    if print_prompt:
        print('\n' + '─' * 60 + '  PROMPT\n')
        print(prompt)
        print('─' * 60)
        return True

    if backend == 'council':
        print(f'Backend:   llm-council  ({Config.COUNCIL_URL})')
    elif backend == 'claude':
        print(f'Backend:   claude CLI  model={Config.CLAUDE_MODEL or "default"}')
    elif backend == 'anthropic':
        print(f'Backend:   anthropic  model={Config.ANTHROPIC_MODEL}')
    elif backend == 'openai':
        print(f'Backend:   openai  model={Config.OPENAI_MODEL}')
    else:
        print(f'Backend:   ollama  model={Config.OLLAMA_MODEL}  ({Config.OLLAMA_URL})')

    print('Calling LLM…', flush=True)
    t0 = time.time()
    try:
        if backend == 'council':
            summary = _call_council(prompt, Config.COUNCIL_URL)
        elif backend == 'claude':
            summary = _call_claude(prompt, Config.CLAUDE_MODEL)
        elif backend == 'anthropic':
            summary = _call_anthropic(prompt, Config.ANTHROPIC_MODEL, Config.ANTHROPIC_API_KEY)
        elif backend == 'openai':
            summary = _call_openai(prompt, Config.OPENAI_MODEL, Config.OPENAI_API_KEY)
        else:
            summary = _call_ollama(prompt, Config.OLLAMA_MODEL, Config.OLLAMA_URL)
    except (RuntimeError, urllib.error.URLError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return False

    elapsed = time.time() - t0
    print(f'Done in {elapsed:.1f}s\n')
    print('─' * 60)
    print(summary)
    print('─' * 60)
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='worklog daily summarizer')
    parser.add_argument(
        '--date',
        type=lambda s: datetime.strptime(s, '%Y-%m-%d').date(),
        default=None,
        metavar='YYYY-MM-DD',
        help='Date to summarize (default: today)',
    )
    parser.add_argument(
        '--backend',
        choices=['ollama', 'council', 'claude', 'anthropic', 'openai'],
        default=None,
        help='Override the backend from config',
    )
    parser.add_argument(
        '--print-prompt',
        action='store_true',
        help='Print the prompt and exit without calling any model',
    )
    args = parser.parse_args()

    ok = summarize(target=args.date, backend=args.backend, print_prompt=args.print_prompt)
    sys.exit(0 if ok else 1)

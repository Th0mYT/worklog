"""
Daily summarizer — reads the JSONL log for a given date and produces
a timesheet-ready summary.

The log is first turned into timesheet *blocks* by summarizer/sessions.py
(durations, gaps, privacy filtering, commessa assignment — all in code). The
LLM is only asked to write one description per block; headers, durations and
the total are assembled here, so a weak or confused model can no longer skew
the numbers, and a missing description falls back to one built from the data.

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
  python -m summarizer.daily_summary --date 2026-06-17 --no-llm   # descriptions built from the data
  python -m summarizer.daily_summary --backend openai   # override config
"""

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from config import Config
from logger.timeutil import entry_time
from summarizer.sessions import UNASSIGNED, Block, DayModel, build_day

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
    return sorted(entries, key=entry_time)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

_MAX_FOCUS = 6
_MAX_COMMITS = 15
_MAX_COMMIT_MSG = 160


def _fmt_hours(h: float) -> str:
    return f'{h:g}h'


def _fmt_minutes(m: float) -> str:
    m = int(round(m))
    return f'{m // 60}h{m % 60:02d}m' if m >= 60 else f'{m}m'


def _hhmm(dt: datetime) -> str:
    return dt.strftime('%H:%M')


def _block_id(i: int) -> str:
    return f'b{i}'


def _format_block(block_id: str, b: Block) -> str:
    label = b.assignment or 'none'
    approx = '  (time estimated: no activity was tracked around the commits)' if b.nominal_minutes else ''
    lines = [f'[{block_id}]  {_hhmm(b.start)}–{_hhmm(b.end)}  {_fmt_hours(b.hours)}  assignment: {label}{approx}']

    mix = b.minutes_by(lambda e: e.get('type')).most_common()
    if len(mix) > 1:
        total = sum(m for _, m in mix) or 1
        lines.append('  mix: ' + ', '.join(f'{name} {round(100 * m / total)}%' for name, m in mix))
    apps = b.top_apps().most_common(4)
    if apps:
        lines.append('  apps: ' + ', '.join(f'{name} {_fmt_minutes(m)}' for name, m in apps))
    focus = b.top_focus().most_common(_MAX_FOCUS)
    if focus:
        lines.append('  activity: ' + '; '.join(f'{name} ({_fmt_minutes(m)})' for name, m in focus))
    branches = b.branches()
    if branches:
        lines.append('  branches: ' + ', '.join(branches[:4]))
    wip = b.wip_files()
    if wip:
        lines.append('  uncommitted files: ' + ', '.join(wip))
    notes = b.notes()
    if notes:
        lines.append('  user notes (own words, use as the base): ' + ' | '.join(notes))
    if b.commits:
        lines.append('  commits:')
        for c in b.commits[:_MAX_COMMITS]:
            e = c.entry
            msg = (e.get('message') or '')[:_MAX_COMMIT_MSG]
            lines.append(f"    {_hhmm(c.ts)} [{e.get('repo', '?')}] {msg}")
        if len(b.commits) > _MAX_COMMITS:
            lines.append(f'    (+{len(b.commits) - _MAX_COMMITS} more commits)')
    return '\n'.join(lines)


def build_prompt(target: date, day: DayModel) -> str:
    blocks = '\n\n'.join(_format_block(_block_id(i), b) for i, b in enumerate(day.blocks, 1))
    return f"""You are helping write a timesheet for {target}.
The activity log has already been analysed: the day is split into numbered BLOCKS whose
durations are final. Your only job is to write ONE short description per block.

What a block shows (most reliable evidence first):
  commits     git commits made during the block — base the description on these
  activity    projects / files / pages / windows with the minutes spent on each
  apps        time per application
  branches, uncommitted files, user notes — extra hints

RULES:
1. Reply with exactly one line per block, in this format and nothing else:
   b1: description
2. The description is one plain sentence (max ~110 characters) saying WHAT was done —
   the feature, bug or topic — not which app was open.
3. Coding blocks: ground it in the commit messages, branch names and file names. Merge
   several commits into one coherent description; do not list them all.
4. Meeting blocks: name the meeting when a title is given.
5. Never invent detail that is not in the block. If the evidence is thin, stay generic
   (e.g. "Development work on <project>").
6. Do not include durations, times or block ids inside the description. No headers,
   no totals, no commentary.
7. Write in the language of the commit messages / user notes (English if there are none).

BLOCKS:
{blocks}
"""


# ---------------------------------------------------------------------------
# Turning the model's answer (or the data) into the final summary
# ---------------------------------------------------------------------------

_DESC_LINE = re.compile(r'^[\s\-*•>#]*\**\[?(b\d+)\]?\**\s*[:.\-–)]\s*(.+?)\s*$', re.IGNORECASE)
_TRAILING_DURATION = re.compile(r'\s*[(\[]\s*\d+(?:\.\d+)?\s*h\s*[)\]]\s*$', re.IGNORECASE)


def _clean_description(text: str) -> str:
    text = _TRAILING_DURATION.sub('', text.replace('**', '').strip().strip('"\''))
    return re.sub(r'\s+', ' ', text)[:220].strip()


def parse_descriptions(text: str, valid_ids: set[str]) -> dict[str, str]:
    """Extract {'b1': 'description'} pairs from the model's reply; unknown ids are ignored."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = _DESC_LINE.match(line)
        if not m:
            continue
        block_id = m.group(1).lower()
        desc = _clean_description(m.group(2))
        if block_id in valid_ids and desc and block_id not in found:
            found[block_id] = desc
    return found


_PREFIX = {
    'coding': 'Development', 'meeting': 'Meeting', 'browser': 'Web research',
    'communication': 'Communication', 'design': 'Design', 'productivity': 'Productivity',
}


def _shorten(text: str, limit: int) -> str:
    """Cut at a word boundary with an ellipsis."""
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(' ', 1)[0].rstrip(' ,;:.') + '…'


def fallback_description(b: Block) -> str:
    """A description built straight from the block's data, for when the model gave none."""
    notes = b.notes()
    if notes:
        return '; '.join(notes)[:200]
    if b.commits:
        msgs = [(c.entry.get('message') or '').strip() for c in b.commits]
        text = '; '.join(_shorten(m, 90) for m in msgs[:2] if m)
        if len(msgs) > 2:
            text += f' (+{len(msgs) - 2} more commits)'
        return text or 'Commits'
    types = b.minutes_by(lambda e: e.get('type'))
    main = types.most_common(1)[0][0] if types else 'other'
    focus = [name for name, _ in b.top_focus().most_common(3)]
    prefix = _PREFIX.get(main, 'Activity')
    return f"{prefix}: {', '.join(focus)}" if focus else prefix


def _reference(b: Block) -> str:
    repos = b.repos()
    if repos:
        return ', '.join(repos[:2])
    apps = b.top_apps().most_common(1)
    return apps[0][0] if apps else ''


def _headers(day: DayModel) -> tuple[list[str | None], str]:
    """Ordered group keys and the mode ('commesse' | 'tags' | 'plain')."""
    present = {b.assignment for b in day.blocks}
    if Config.COMMESSE:
        order: list[str | None] = [c['name'] for c in Config.COMMESSE if c['name'] in present]
        if None in present:
            order.append(None)
        return order, 'commesse'
    if any(present):
        order = sorted((a for a in present if a), key=lambda a: min(
            b.start for b in day.blocks if b.assignment == a))
        if None in present:
            order.append(None)
        return order, 'tags'
    return [None], 'plain'


def render_summary(day: DayModel, descriptions: dict[str, str]) -> str:
    ids = {id(b): _block_id(i) for i, b in enumerate(day.blocks, 1)}
    order, mode = _headers(day)
    out: list[str] = []
    for key in order:
        group = [b for b in day.blocks if b.assignment == key]
        if mode != 'plain':
            out.append(f'## {key if key else (UNASSIGNED if mode == "commesse" else "General")}')
        for b in group:
            desc = descriptions.get(ids[id(b)]) or fallback_description(b)
            ref = _reference(b)
            out.append(f'{desc} ({_fmt_hours(b.hours)})' + (f' [{ref}]' if ref else ''))
    out += ['', f'Total: {_fmt_hours(day.total_hours)}']
    return '\n'.join(out)


def _describe_exclusions(day: DayModel) -> list[str]:
    labels = {
        'private': 'private browsing', 'personal-domain': 'personal sites',
        'unclassified-domain': 'unclassified sites', 'marked-personal': 'entries marked personal',
        'ignored-app': 'ignored apps', 'short': 'blocks under 5 min',
    }
    lines = []
    parts = [f'{labels.get(k, k)} {_fmt_minutes(m)}' for k, m in day.excluded_minutes.most_common() if m >= 1]
    if parts:
        lines.append('Left out:  ' + ', '.join(parts))
    if day.unclassified_domains:
        top = ', '.join(d for d, _ in day.unclassified_domains.most_common(5))
        lines.append(f'Tip:       classify these sites as work/personal in Settings › Browser: {top}')
    return lines


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
# Persistence — save the generated summary so it is not lost
# ---------------------------------------------------------------------------

def _save_summary(target: date, summary: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'{target}.md'
    path.write_text(summary.rstrip() + '\n', encoding='utf-8')
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def summarize(
    target: date | None = None,
    log_dir: Path | None = None,
    backend: str | None = None,
    print_prompt: bool = False,
    no_llm: bool = False,
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
    marker_count   = sum(1 for e in entries if e.get('marker'))
    activity_count = len(entries) - git_count - marker_count
    print(f'Entries:   {activity_count} activity entries, {git_count} git commits')

    day = build_day(entries)
    for line in _describe_exclusions(day):
        print(line)
    if not day.blocks:
        print(f'ERROR: nothing to summarize for {target} — every entry was filtered out or too short', file=sys.stderr)
        return False
    print(f'Blocks:    {len(day.blocks)}  ({_fmt_hours(day.total_hours)} in total)')

    prompt = build_prompt(target, day)

    if print_prompt:
        print('\n' + '─' * 60 + '  PROMPT\n')
        print(prompt)
        print('─' * 60)
        return True

    if no_llm:
        print('Backend:   none (descriptions built from the data, nothing saved)')
        print('─' * 60)
        print(render_summary(day, {}))
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

    print(f'Prompt:    {len(prompt):,} chars', flush=True)
    print('Calling LLM…', flush=True)
    t0 = time.time()
    try:
        if backend == 'council':
            reply = _call_council(prompt, Config.COUNCIL_URL)
        elif backend == 'claude':
            reply = _call_claude(prompt, Config.CLAUDE_MODEL)
        elif backend == 'anthropic':
            reply = _call_anthropic(prompt, Config.ANTHROPIC_MODEL, Config.ANTHROPIC_API_KEY)
        elif backend == 'openai':
            reply = _call_openai(prompt, Config.OPENAI_MODEL, Config.OPENAI_API_KEY)
        else:
            reply = _call_ollama(prompt, Config.OLLAMA_MODEL, Config.OLLAMA_URL)
    except (RuntimeError, urllib.error.URLError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return False

    elapsed = time.time() - t0
    print(f'Done in {elapsed:.1f}s')

    ids = {_block_id(i) for i in range(1, len(day.blocks) + 1)}
    descriptions = parse_descriptions(reply, ids)
    if len(descriptions) < len(ids):
        print(f'WARNING: the model described {len(descriptions)} of {len(ids)} blocks — '
              f'the rest use descriptions built from the data', file=sys.stderr)
    summary = render_summary(day, descriptions)

    try:
        saved_path = _save_summary(target, summary, Path(Config.SUMMARIES_DIR))
        print(f'Saved:     {saved_path}\n')
    except OSError as e:
        print(f'WARNING: could not save summary: {e}\n', file=sys.stderr)
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
    parser.add_argument(
        '--no-llm',
        action='store_true',
        help='Print the summary with descriptions built from the data — no model call, nothing saved',
    )
    parser.add_argument(
        '--log-dir',
        type=Path,
        default=None,
        help='Read the daily log from this directory instead of the configured one',
    )
    args = parser.parse_args()

    ok = summarize(target=args.date, log_dir=args.log_dir, backend=args.backend,
                   print_prompt=args.print_prompt, no_llm=args.no_llm)
    sys.exit(0 if ok else 1)

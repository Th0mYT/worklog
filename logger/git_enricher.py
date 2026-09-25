"""
Git enricher — reads commits from configured repos for a given date
and appends them to the daily JSONL log alongside activity-poller entries.

Each entry written:
  {
    "ts":            "2026-06-18T17:39:23",      # local time, no offset (see logger/timeutil.py)
    "source":        "git",
    "repo":          "my-api",
    "type":          "coding",
    "commit":        "3e2f2f17",
    "branch":        "feature/KH-682-assignment",
    "message":       "feat(assignment): add event relation to member assignment controller",
    "files_changed": 1,
    "insertions":    1,
    "deletions":     0,
    "files":         ["src/assignment/controller.ts"]
  }

Commits are read from every local branch, not just the checked-out one.

Entries are deduplicated by commit hash so the enricher is safe to run
multiple times for the same day.

Usage:
  python -m logger.git_enricher                  # enrich today
  python -m logger.git_enricher --date 2026-06-17
  python -m logger.git_enricher --date 2026-06-17 --dry-run
"""

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from config import Config
from logger.timeutil import normalize_ts


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(repo: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ['git', '-C', repo, *args],
            capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip()
    except Exception:
        return ''


def _repo_name(repo_path: str) -> str:
    return Path(repo_path).name


def _discover_repos(workspace: str) -> list[str]:
    """Return direct subdirectories of workspace that are git repos."""
    ws = Path(workspace).expanduser()
    if not ws.is_dir():
        print(f'[worklog] git_enricher: workspace not found: {workspace}', file=sys.stderr)
        return []
    found = sorted(str(p) for p in ws.iterdir() if p.is_dir() and (p / '.git').exists())
    print(f'[worklog] git_enricher: workspace {ws} → {len(found)} repo(s) found')
    return found


def _resolve_repos() -> list[tuple[str, list[str]]]:
    """Return (path, tags) pairs from explicit repos + workspace discovery.

    Workspace repos inherit the workspace's tags.
    """
    seen: set[str] = set()
    repos: list[tuple[str, list[str]]] = []

    for p in Config.GIT_REPOS:
        key = str(Path(p).expanduser().resolve())
        if key not in seen:
            seen.add(key)
            repos.append((p, Config.GIT_REPO_TAGS.get(p, [])))

    for workspace in Config.GIT_WORKSPACES:
        ws_tags = Config.GIT_REPO_TAGS.get(workspace, [])
        for p in _discover_repos(workspace):
            key = str(Path(p).resolve())
            if key not in seen:
                seen.add(key)
                repos.append((p, ws_tags))

    return repos


_FIELD_SEP = '\x1f'
_MAX_FILES = 10


def _parse_commit_lines(raw: str) -> list[dict]:
    """Parse `git log --format=%H<US>%cI<US>%S<US>%s` output into commit dicts."""
    commits = []
    for line in raw.splitlines():
        parts = line.split(_FIELD_SEP, 3)
        if len(parts) != 4:
            continue
        source = parts[2].strip()
        for prefix in ('refs/heads/', 'refs/'):
            if source.startswith(prefix):
                source = source[len(prefix):]
                break
        commits.append({
            'hash': parts[0].strip(),
            'ts': normalize_ts(parts[1].strip()),
            'branch': source,
            'message': parts[3].strip(),
        })
    return commits


def _commits_for_date(repo_path: str, target: date, author: str) -> list[dict]:
    """Return a list of {hash, ts, branch, message} for commits on target date, on any local branch."""
    since = f'{target} 00:00:00'
    until = f'{target} 23:59:59'

    cmd_args = [
        'log',
        '--branches',
        '--source',
        f'--since={since}',
        f'--until={until}',
        f'--format=%H{_FIELD_SEP}%cI{_FIELD_SEP}%S{_FIELD_SEP}%s',
    ]
    if author:
        cmd_args.append(f'--author={author}')

    raw = _run_git(repo_path, *cmd_args)
    return _parse_commit_lines(raw) if raw else []


def _parse_numstat(raw: str) -> tuple[int, int, int, list[str]]:
    """Parse `git show --numstat --format=` into (files_changed, insertions, deletions, paths)."""
    files: list[str] = []
    ins = dels = 0
    for line in raw.splitlines():
        cols = line.split('\t', 2)
        if len(cols) != 3:
            continue
        # Binary files report '-' for both counts.
        ins += int(cols[0]) if cols[0].isdigit() else 0
        dels += int(cols[1]) if cols[1].isdigit() else 0
        files.append(cols[2].strip())
    return len(files), ins, dels, files


def _commit_details(repo_path: str, commit_hash: str) -> tuple[int, int, int, list[str]]:
    """Return (files_changed, insertions, deletions, first paths) for a single commit."""
    raw = _run_git(repo_path, 'show', '--numstat', '--format=', commit_hash)
    changed, ins, dels, files = _parse_numstat(raw)
    return changed, ins, dels, files[:_MAX_FILES]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _existing_hashes(log_file: Path) -> set[str]:
    """Return commit hashes already present in the log file."""
    hashes: set[str] = set()
    if not log_file.exists():
        return hashes
    with log_file.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get('source') == 'git' and 'commit' in entry:
                    hashes.add(entry['commit'])
            except json.JSONDecodeError:
                continue
    return hashes


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def enrich(target: date | None = None, log_dir: Path | None = None, dry_run: bool = False) -> int:
    """
    Append git commits for `target` (default: today) to the daily log.
    Returns the number of new entries written.
    """
    target = target or date.today()
    log_dir = log_dir or Path(Config.LOGS_DIR)
    log_file = log_dir / f'{target}.jsonl'

    repo_list = _resolve_repos()
    if not repo_list:
        print('[worklog] git_enricher: no repos configured (set git_repos or git_workspaces)', file=sys.stderr)
        return 0
    print(f'[worklog] git_enricher: scanning {len(repo_list)} repo(s) for {target}')

    existing = _existing_hashes(log_file)
    new_entries: list[dict] = []

    for repo_path, tags in repo_list:
        if not Path(repo_path).is_dir():
            print(f'[worklog] git_enricher: skipping missing repo {repo_path}', file=sys.stderr)
            continue

        commits = _commits_for_date(repo_path, target, Config.GIT_AUTHOR)
        repo = _repo_name(repo_path)

        for c in commits:
            short = c['hash'][:8]
            if short in existing:
                continue

            changed, ins, dels, files = _commit_details(repo_path, c['hash'])
            entry: dict = {
                'ts': c['ts'],
                'source': 'git',
                'repo': repo,
                'type': 'coding',
                'commit': short,
                'message': c['message'],
                'files_changed': changed,
                'insertions': ins,
                'deletions': dels,
            }
            if c['branch']:
                entry['branch'] = c['branch']
            if files:
                entry['files'] = files
            if tags:
                entry['tags'] = tags
            new_entries.append(entry)
            existing.add(short)

    if not new_entries:
        print(f'[worklog] git_enricher: no new commits for {target}')
        return 0

    # Sort by timestamp before writing
    new_entries.sort(key=lambda e: e['ts'])

    if dry_run:
        for e in new_entries:
            print(json.dumps(e))
        return len(new_entries)

    log_dir.mkdir(parents=True, exist_ok=True)
    with log_file.open('a') as f:
        for e in new_entries:
            f.write(json.dumps(e) + '\n')

    print(f'[worklog] git_enricher: wrote {len(new_entries)} commit(s) to {log_file}')
    return len(new_entries)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='worklog git enricher')
    parser.add_argument(
        '--date',
        type=lambda s: datetime.strptime(s, '%Y-%m-%d').date(),
        default=None,
        metavar='YYYY-MM-DD',
        help='Date to enrich (default: today)',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print entries to stdout without writing to the log',
    )
    args = parser.parse_args()

    enrich(target=args.date, dry_run=args.dry_run)

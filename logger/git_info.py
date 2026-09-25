"""
Cheap read-only git lookups used while polling: the checked-out branch (a branch
named after a ticket is often the best hint of what you are working on) and the
files with uncommitted changes (work in progress that has no commit yet).
"""

import subprocess
from pathlib import Path


def _git(repo: str, *args: str, timeout: float = 3) -> str:
    try:
        r = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''


def current_branch(repo: str) -> str:
    """Checked-out branch name ('' when detached or not a repo). No subprocess in the common case."""
    head = Path(repo).expanduser() / '.git' / 'HEAD'
    try:
        text = head.read_text().strip()
    except OSError:
        # `.git` may be a file (worktree / submodule): let git resolve it.
        branch = _git(repo, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
        return '' if branch == 'HEAD' else branch
    prefix = 'ref: refs/heads/'
    return text[len(prefix):] if text.startswith(prefix) else ''


def wip_files(repo: str, limit: int = 8) -> list[str]:
    """Paths with uncommitted changes (modified, added, untracked), capped at `limit`."""
    out = _git(repo, 'status', '--porcelain', '--untracked-files=normal')
    files: list[str] = []
    for line in out.splitlines():
        path = line[3:].strip()
        if ' -> ' in path:  # rename: keep the new name
            path = path.split(' -> ', 1)[1]
        path = path.strip('"')
        if path:
            files.append(path)
        if len(files) >= limit:
            break
    return files

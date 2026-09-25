"""
Pure helpers that turn raw window/tab/path strings into structured signals.

Nothing here touches macOS or the filesystem beyond reading `.git` markers, so
it is all unit-testable:

  * IDE window titles   → (project, file)
  * browser URLs        → domain, query-less URL, work/personal/unknown class
  * paths               → git repo root
  * project / repo name → configured repo tags
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# IDE window titles
# ---------------------------------------------------------------------------

_JETBRAINS = {
    'pycharm', 'webstorm', 'intellij idea', 'goland', 'phpstorm', 'rider', 'clion',
    'rubymine', 'datagrip', 'android studio', 'appcode', 'rustrover',
}
_VSCODE_LIKE = {
    'cursor', 'visual studio code', 'code', 'windsurf', 'vscodium', 'zed', 'sublime text',
}
_XCODE = {'xcode'}

_SEPARATORS = (' — ', ' – ', ' - ', ' ‒ ')
_TITLE_NOISE = re.compile(
    r'\s*(\[Extension Development Host\]|\(Workspace\)|\[SSH:[^\]]*\]|\[WSL:[^\]]*\])\s*$',
    re.IGNORECASE,
)
_PATH_BRACKET = re.compile(r'\s*\[[^\]]*\]\s*$')


def _split_title(window: str) -> list[str]:
    parts = [window]
    for sep in _SEPARATORS:
        nxt: list[str] = []
        for p in parts:
            nxt.extend(p.split(sep))
        parts = nxt
    return [p.strip() for p in parts if p.strip()]


def _looks_like_file(text: str) -> bool:
    return '/' in text or bool(re.search(r'\.[A-Za-z0-9]{1,8}$', text))


def parse_ide_title(app: str, window: str) -> tuple[str, str]:
    """Return (project, file) parsed from an IDE window title; '' when unknown.

    Only file *basenames* are returned — a full path adds nothing to a
    timesheet and would be noise in the log.
    """
    if not window:
        return '', ''
    app_l = app.lower()
    # Drop the IDE's own name wherever it appears: the process may be called
    # "Code" while the title ends in "Visual Studio Code".
    product_names = _JETBRAINS | _VSCODE_LIKE | _XCODE | {app_l}
    parts = [p for p in _split_title(window) if p.lower() not in product_names]
    parts = [_TITLE_NOISE.sub('', p).lstrip('●• ').strip() for p in parts]
    parts = [p for p in parts if p]
    if not parts:
        return '', ''

    project = file = ''
    if app_l in _JETBRAINS:
        # "project – file"   or   "project [~/path] – …/file"
        project = _PATH_BRACKET.sub('', parts[0]).strip()
        if len(parts) > 1 and _looks_like_file(parts[-1]):
            file = parts[-1]
    elif app_l in _VSCODE_LIKE:
        # "file — project"   or just "project"
        if len(parts) >= 2:
            file, project = parts[0], parts[-1]
        elif _looks_like_file(parts[0]):
            file = parts[0]
        else:
            project = parts[0]
    elif app_l in _XCODE:
        # "project — file"
        project = parts[0]
        if len(parts) > 1:
            file = parts[-1]
    else:
        return '', ''

    file = file.rsplit('/', 1)[-1]
    if project.lower() == app_l:
        project = ''
    return project, file


# ---------------------------------------------------------------------------
# Paths → repo
# ---------------------------------------------------------------------------

_TITLE_PATH = re.compile(r'(~|/)[^\s:|]*')


def path_from_title(window: str) -> str:
    """Best-effort: the last '~/…' or '/…' path mentioned in a terminal title."""
    matches = [m.group(0) for m in _TITLE_PATH.finditer(window or '')]
    return matches[-1] if matches else ''


_ROOT_CACHE: dict[str, str] = {}


def find_repo_root(path: str) -> str:
    """Nearest ancestor of `path` containing `.git`, or '' if there is none."""
    if not path:
        return ''
    try:
        p = Path(path).expanduser()
    except (RuntimeError, ValueError):
        return ''
    key = str(p)
    if key in _ROOT_CACHE:
        return _ROOT_CACHE[key]
    root = ''
    for cand in [p, *p.parents]:
        try:
            if (cand / '.git').exists():
                root = str(cand)
                break
        except OSError:
            break
    if len(_ROOT_CACHE) > 512:
        _ROOT_CACHE.clear()
    _ROOT_CACHE[key] = root
    return root


# ---------------------------------------------------------------------------
# Repo index — which repo names are known, and which tags they carry
# ---------------------------------------------------------------------------

@dataclass
class RepoInfo:
    name: str
    path: str
    tags: list[str] = field(default_factory=list)


def build_repo_index(repos, workspaces, repo_tags) -> dict[str, RepoInfo]:
    """Map lowercase repo folder name → RepoInfo for every configured repo.

    Workspace children inherit the workspace's tags. Untagged repos are kept
    too: they still give the poller a project name and a path for git lookups.
    """
    index: dict[str, RepoInfo] = {}

    for raw in repos:
        p = Path(raw).expanduser()
        index[p.name.lower()] = RepoInfo(p.name, str(p), list(repo_tags.get(raw, [])))

    for raw in workspaces:
        ws_tags = list(repo_tags.get(raw, []))
        try:
            for sub in sorted(Path(raw).expanduser().iterdir()):
                if sub.is_dir() and (sub / '.git').exists():
                    index.setdefault(sub.name.lower(), RepoInfo(sub.name, str(sub), ws_tags))
        except OSError:
            pass

    return index


def match_repo(index: dict[str, RepoInfo], project: str = '', window: str = '') -> RepoInfo | None:
    """Resolve a repo: exact project-name hit first, then longest name found in the title."""
    if project:
        hit = index.get(project.lower())
        if hit:
            return hit
    w = (window or '').lower()
    if w:
        for name in sorted(index, key=len, reverse=True):
            # whole-token match: repo `api` must not fire on "Rapid" or "happy-channel"
            if re.search(r'(?<![a-z0-9_-])' + re.escape(name) + r'(?![a-z0-9_-])', w):
                return index[name]
    return None


# ---------------------------------------------------------------------------
# Browser URLs and domain rules
# ---------------------------------------------------------------------------

def domain_of(url: str) -> str:
    """Lowercase hostname without 'www.'; '' when the URL has none."""
    if not url:
        return ''
    try:
        host = urlsplit(url if '://' in url else f'http://{url}').hostname or ''
    except ValueError:
        return ''
    host = host.lower()
    return host[4:] if host.startswith('www.') else host


def strip_url(url: str) -> str:
    """scheme://host[:port]/path — no credentials, query string or fragment."""
    if not url:
        return ''
    try:
        parts = urlsplit(url)
        host = parts.hostname or ''
        if not (parts.scheme and host):
            return ''
        port = f':{parts.port}' if parts.port else ''
        return f'{parts.scheme}://{host}{port}{parts.path}'
    except ValueError:
        return ''


def _rule_matches(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith('.' + rule)


def classify_domain(domain: str, rules: dict[str, list[str]]) -> str:
    """'work', 'personal' or 'unknown'. The most specific (longest) rule wins; a tie is personal."""
    if not domain:
        return 'unknown'
    best_len, best = -1, 'unknown'
    for side in ('personal', 'work'):
        for rule in rules.get(side, []):
            if rule and _rule_matches(domain, rule) and len(rule) > best_len:
                best_len, best = len(rule), side
    return best


def keeps_detail(site_class: str, unknown_policy: str) -> bool:
    """Should a page of this class keep its title/URL (and count as work time)?"""
    return site_class == 'work' or (site_class == 'unknown' and unknown_policy == 'work')


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def parse_lsof_cwd(output: str) -> str:
    """Extract the path from `lsof -Fn` output (the line starting with 'n')."""
    for line in (output or '').splitlines():
        if line.startswith('n') and len(line) > 1:
            return line[1:]
    return ''


def pick_foreground_pid(ps_output: str) -> int:
    """From `ps -o pid=,stat=,comm=` rows for a tty, the newest foreground (stat has '+') process."""
    best = 0
    for line in (ps_output or '').splitlines():
        cols = line.split(None, 2)
        if len(cols) < 2 or '+' not in cols[1]:
            continue
        try:
            best = max(best, int(cols[0]))
        except ValueError:
            continue
    return best

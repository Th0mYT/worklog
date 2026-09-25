"""
Turns a day's raw log into timesheet blocks — deterministically, in code.

Handing a small LLM one line per snapshot and asking it to work out durations,
gaps, project assignment and totals is what made summaries vague. Everything
that is arithmetic or rule-matching is done here instead:

  1. timestamps are normalised (git and poller use different formats)
  2. entries become time-bounded samples: each lasts until the next entry, capped
     at 1.5 heartbeats, trimmed by idle/pause/stop markers, and cut by manual
     time blocks (which win over the automatic samples they overlap)
  3. anything private, personal or unclassified is dropped, and only counted
  4. samples and commits are assigned to a commessa (or tag) by keyword rules
  5. samples are grouped into sessions (gap > 30 min = new session) and, inside
     a session, into one block per assignment

The LLM is then only asked to *describe* each block; durations, headers and the
total are computed here (see daily_summary.py).
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config import Config
from logger.extractors import classify_domain, domain_of, keeps_detail
from logger.timeutil import parse_ts

SESSION_GAP = timedelta(minutes=30)
COMMIT_ATTACH_SLACK = timedelta(minutes=10)
INHERIT_WINDOW = timedelta(minutes=120)
MIN_BLOCK_MINUTES = 5
ORPHAN_COMMIT_MINUTES = 15
ROUND_TO_HOURS = 0.25
UNASSIGNED = 'Non assegnata'

_GAP_BEGIN = {'idle_start', 'pause', 'stop'}
_GAP_END = {'idle_end', 'resume', 'start'}

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    start: datetime
    end: datetime
    entry: dict
    assignment: str | None = None

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class Commit:
    ts: datetime
    entry: dict
    assignment: str | None = None


@dataclass
class Block:
    assignment: str | None
    samples: list[Sample] = field(default_factory=list)
    commits: list[Commit] = field(default_factory=list)
    nominal_minutes: float = 0.0     # time assumed for commits with no tracked activity around them

    @property
    def minutes(self) -> float:
        return sum(s.minutes for s in self.samples) + self.nominal_minutes

    @property
    def start(self) -> datetime:
        times = [s.start for s in self.samples] + [c.ts for c in self.commits]
        return min(times)

    @property
    def end(self) -> datetime:
        times = [s.end for s in self.samples] + [c.ts for c in self.commits]
        return max(times)

    @property
    def hours(self) -> float:
        """Duration rounded to the timesheet grain (never below one grain for a kept block)."""
        raw = self.minutes / 60
        return max(ROUND_TO_HOURS, round(raw / ROUND_TO_HOURS) * ROUND_TO_HOURS)

    @property
    def tags(self) -> list[str]:
        seen: list[str] = []
        for e in [*(s.entry for s in self.samples), *(c.entry for c in self.commits)]:
            for t in e.get('tags') or []:
                if t not in seen:
                    seen.append(t)
        return seen

    def minutes_by(self, key) -> Counter:
        counter: Counter = Counter()
        for s in self.samples:
            label = key(s.entry)
            if label:
                counter[label] += s.minutes
        return counter

    def top_apps(self) -> Counter:
        return self.minutes_by(lambda e: e.get('app'))

    def top_focus(self) -> Counter:
        return self.minutes_by(focus_label)

    def branches(self) -> list[str]:
        seen: list[str] = []
        for e in [*(s.entry for s in self.samples), *(c.entry for c in self.commits)]:
            b = e.get('branch')
            if b and b not in seen:
                seen.append(b)
        return seen

    def wip_files(self, limit: int = 8) -> list[str]:
        seen: list[str] = []
        for s in self.samples:
            for f in s.entry.get('wip_files') or []:
                if f not in seen:
                    seen.append(f)
        return seen[:limit]

    def notes(self) -> list[str]:
        seen: list[str] = []
        for s in self.samples:
            n = (s.entry.get('note') or '').strip()
            if n and n not in seen:
                seen.append(n)
        return seen

    def repos(self) -> list[str]:
        """Repos/projects touched, most time (then most commits) first."""
        weight: Counter = Counter()
        for s in self.samples:
            p = s.entry.get('project')
            if p:
                weight[p] += s.minutes
        for c in self.commits:
            r = c.entry.get('repo')
            if r:
                weight[r] += 1
        return [name for name, _ in weight.most_common()]


@dataclass
class DayModel:
    blocks: list[Block]
    excluded_minutes: Counter
    unclassified_domains: Counter

    @property
    def total_hours(self) -> float:
        return sum(b.hours for b in self.blocks)


def focus_label(entry: dict) -> str:
    """What a sample was 'about', as a short human string."""
    kind = entry.get('type', 'other')
    app = entry.get('app', '')
    note = (entry.get('note') or '').strip()
    if note:
        return note
    if kind == 'browser':
        domain = entry.get('domain', '')
        title = entry.get('tab_title', '')
        return f'{domain}: {title}' if domain and title else (domain or title or app)
    if kind == 'coding':
        project, file = entry.get('project', ''), entry.get('file', '')
        if project and file:
            return f'{project} › {file}'
        if project:
            return project
    window = entry.get('window', '')
    return f'{app} — {window}' if window else app


# ---------------------------------------------------------------------------
# Commessa / tag assignment
# ---------------------------------------------------------------------------

def make_assigner(commesse: list[dict]):
    """Return assign(texts, tags) -> commessa name or None.

    A commessa matches when one of its keywords appears as a whole token in any
    of the texts (repo, branch, window/file, commit message, note, tags…). The
    client name counts as a keyword only when a single commessa has that client.
    The commessa with the most keyword characters matched wins.
    """
    clients = Counter((c.get('client') or '').lower() for c in commesse if c.get('client'))
    rules: list[tuple[str, re.Pattern]] = []
    for c in commesse:
        words = list(c.get('keywords') or [])
        client = c.get('client') or ''
        if client and clients[client.lower()] == 1:
            words.append(client)
        for w in words:
            if w.strip():
                pattern = r'(?<![A-Za-z0-9])' + re.escape(w.strip()) + r'(?![A-Za-z0-9])'
                rules.append((c['name'], re.compile(pattern, re.IGNORECASE)))
    order = {c['name']: i for i, c in enumerate(commesse)}

    def assign(texts, tags=()) -> str | None:
        hay = '\n'.join(str(t) for t in [*texts, *tags] if t)
        if not hay:
            return None
        score: Counter = Counter()
        for name, rx in rules:
            m = rx.search(hay)
            if m:
                score[name] += len(m.group(0))
        if not score:
            return None
        return max(score, key=lambda n: (score[n], -order[n]))

    return assign


def _sample_texts(e: dict) -> list:
    return [e.get(k) for k in ('window', 'tab_title', 'url', 'domain', 'project', 'file', 'branch', 'note')] \
        + list(e.get('wip_files') or [])


def _commit_texts(e: dict) -> list:
    return [e.get('message'), e.get('repo'), e.get('branch')] + list(e.get('files') or [])


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------

def _gaps(markers: list[tuple[datetime, str]]) -> list[tuple[datetime, datetime | None]]:
    """[(begin, end|None)] — spans during which the user was not there."""
    gaps: list[tuple[datetime, datetime | None]] = []
    open_at: datetime | None = None
    for ts, kind in sorted(markers, key=lambda m: m[0]):
        if kind in _GAP_BEGIN:
            if open_at is None:
                open_at = ts
        elif kind in _GAP_END and open_at is not None:
            gaps.append((open_at, ts))
            open_at = None
    if open_at is not None:
        gaps.append((open_at, None))
    return gaps


def _in_gap(ts: datetime, gaps) -> bool:
    return any(begin <= ts and (end is None or ts < end) for begin, end in gaps)


def _subtract(start: datetime, end: datetime, cutters) -> list[tuple[datetime, datetime]]:
    pieces = [(start, end)]
    for cs, ce in cutters:
        nxt = []
        for a, b in pieces:
            if ce <= a or cs >= b:
                nxt.append((a, b))
                continue
            if a < cs:
                nxt.append((a, cs))
            if ce < b:
                nxt.append((ce, b))
        pieces = nxt
    return pieces


def _verdict(entry: dict, rules, unknown_policy) -> tuple[bool, str, str]:
    """(kept, reason, domain) for a sample — reason is why it was dropped."""
    if entry.get('excluded'):
        return False, entry.get('reason') or 'marked-personal', ''
    if entry.get('type') == 'browser':
        domain = entry.get('domain') or domain_of(entry.get('url', ''))
        cls = classify_domain(domain, rules) if domain else 'unknown'
        if keeps_detail(cls, unknown_policy):
            return True, '', domain
        return False, ('personal-domain' if cls == 'personal' else 'unclassified-domain'), domain
    return True, '', ''


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_day(entries: list[dict], *, commesse: list[dict] | None = None,
              rules: dict | None = None, unknown_policy: str | None = None,
              heartbeat: int | None = None) -> DayModel:
    commesse = Config.COMMESSE if commesse is None else commesse
    rules = Config.BROWSER_RULES if rules is None else rules
    unknown_policy = Config.BROWSER_UNKNOWN if unknown_policy is None else unknown_policy
    heartbeat = Config.POLL_INTERVAL if heartbeat is None else heartbeat
    assign_commessa = make_assigner(commesse)

    logged_heartbeats: list[float] = []
    timeline: list[tuple[datetime, dict]] = []
    commits: list[Commit] = []
    manual: list[tuple[datetime, datetime, dict]] = []
    markers: list[tuple[datetime, str]] = []

    for e in entries:
        ts = parse_ts(e.get('ts'))
        if ts is None:
            continue
        if e.get('marker'):
            markers.append((ts, e['marker']))
            if isinstance(e.get('heartbeat'), (int, float)) and e['heartbeat'] > 0:
                logged_heartbeats.append(e['heartbeat'])
        elif e.get('source') == 'git':
            if not e.get('excluded'):
                commits.append(Commit(ts, e))
        elif e.get('manual') and e.get('end_ts'):
            end = parse_ts(e['end_ts'])
            if end and end > ts:
                manual.append((ts, end, e))
        else:
            timeline.append((ts, e))

    # The poller that wrote the log may have used a longer heartbeat than the current config.
    heartbeat = max([heartbeat, *logged_heartbeats])
    cap = timedelta(seconds=heartbeat * 1.5)

    gaps = _gaps(markers)
    timeline.sort(key=lambda item: item[0])
    timeline = [(ts, e) for ts, e in timeline if not _in_gap(ts, gaps)]
    manual_spans = [(s, e) for s, e, _ in manual]

    excluded: Counter = Counter()
    unclassified: Counter = Counter()
    samples: list[Sample] = []

    def add_sample(start: datetime, end: datetime, entry: dict, cut: bool) -> None:
        pieces = _subtract(start, end, manual_spans) if cut else [(start, end)]
        for a, b in pieces:
            kept, reason, domain = _verdict(entry, rules, unknown_policy)
            minutes = (b - a).total_seconds() / 60
            if not kept:
                excluded[reason] += minutes
                if reason == 'unclassified-domain':
                    unclassified[domain or '(unknown)'] += minutes
                continue
            samples.append(Sample(a, b, entry))

    for i, (ts, e) in enumerate(timeline):
        nxt = timeline[i + 1][0] if i + 1 < len(timeline) else ts + timedelta(seconds=heartbeat)
        end = min(nxt, ts + cap)
        for begin, _ in gaps:
            if ts < begin < end:
                end = begin
                break
        if end > ts:
            add_sample(ts, end, e, cut=True)
    for start, end, e in manual:
        add_sample(start, end, e, cut=False)
    samples.sort(key=lambda s: s.start)

    # --- assignment -----------------------------------------------------------
    tag_mode = not commesse
    for s in samples:
        s.assignment = _assignment(s.entry, _sample_texts(s.entry), assign_commessa, tag_mode)
    for c in commits:
        c.assignment = _assignment(c.entry, _commit_texts(c.entry), assign_commessa, tag_mode)
    commits.sort(key=lambda c: c.ts)
    if not tag_mode:
        _inherit_from_commits(samples, commits)

    # --- sessions and blocks --------------------------------------------------
    sessions: list[list[Sample]] = []
    for s in samples:
        if sessions and s.start - max(x.end for x in sessions[-1]) <= SESSION_GAP:
            sessions[-1].append(s)
        else:
            sessions.append([s])

    session_blocks: list[dict[str | None, Block]] = [{} for _ in sessions]
    for idx, session in enumerate(sessions):
        for s in session:
            session_blocks[idx].setdefault(s.assignment, Block(s.assignment)).samples.append(s)

    orphans: list[Commit] = []
    for c in commits:
        idx = _session_for(c.ts, sessions)
        if idx is None:
            orphans.append(c)
        else:
            session_blocks[idx].setdefault(c.assignment, Block(c.assignment)).commits.append(c)

    blocks = [b for group in session_blocks for b in group.values()]
    blocks.extend(_orphan_blocks(orphans))

    kept: list[Block] = []
    for b in blocks:
        if b.minutes < MIN_BLOCK_MINUTES and not b.commits:
            excluded['short'] += b.minutes
            continue
        kept.append(b)
    kept.sort(key=lambda b: (b.start, b.assignment or ''))
    return DayModel(kept, excluded, unclassified)


def _assignment(entry: dict, texts: list, assign_commessa, tag_mode: bool) -> str | None:
    tags = entry.get('tags') or []
    if tag_mode:
        return f'#{tags[0]}' if tags else None
    return assign_commessa(texts, tags)


def _inherit_from_commits(samples: list[Sample], commits: list[Commit]) -> None:
    """Unassigned work in a repo takes the commessa of that repo's next assigned commit (≤ 2 h later)."""
    by_repo: dict[str, list[Commit]] = {}
    for c in commits:
        if c.assignment:
            by_repo.setdefault((c.entry.get('repo') or '').lower(), []).append(c)
    for s in samples:
        project = (s.entry.get('project') or '').lower()
        if s.assignment or not project:
            continue
        for c in by_repo.get(project, []):
            if s.end - timedelta(minutes=1) <= c.ts <= s.end + INHERIT_WINDOW:
                s.assignment = c.assignment
                break


def _session_for(ts: datetime, sessions: list[list[Sample]]) -> int | None:
    best, best_dist = None, None
    for i, session in enumerate(sessions):
        start = min(s.start for s in session)
        end = max(s.end for s in session)
        if start - COMMIT_ATTACH_SLACK <= ts <= end + COMMIT_ATTACH_SLACK:
            dist = timedelta(0) if start <= ts <= end else min(abs(ts - start), abs(ts - end))
            if best_dist is None or dist < best_dist:
                best, best_dist = i, dist
    return best


def _orphan_blocks(orphans: list[Commit]) -> list[Block]:
    """Commits with no tracked activity nearby: one nominal-time block per (cluster, assignment)."""
    blocks: list[Block] = []
    cluster: list[Commit] = []

    def flush() -> None:
        groups: dict[str | None, Block] = {}
        for c in cluster:
            groups.setdefault(c.assignment, Block(c.assignment, nominal_minutes=ORPHAN_COMMIT_MINUTES)).commits.append(c)
        blocks.extend(groups.values())

    for c in orphans:
        if cluster and c.ts - cluster[-1].ts > SESSION_GAP:
            flush()
            cluster = []
        cluster.append(c)
    if cluster:
        flush()
    return blocks

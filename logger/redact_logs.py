"""
One-off cleanup of logs written before the privacy rules existed.

Older entries may hold full URLs (query strings included), titles of pages on
personal or unclassified sites, and window titles of chat apps. This applies
the same policy the poller uses today to what is already on disk, and
normalises git timestamps ("2026-09-24 09:40:54 +0200" → "2026-09-24T09:40:54")
so they sort correctly next to poller entries.

  python -m logger.redact_logs                 # dry run: report what would change
  python -m logger.redact_logs --apply         # rewrite the files (a .bak copy is kept; stop the poller first)
  python -m logger.redact_logs --dir ~/somewhere

Idempotent: running it twice changes nothing the second time.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from config import Config
from logger.extractors import classify_domain, domain_of, keeps_detail, strip_url
from logger.logio import rewrite_atomic
from logger.timeutil import normalize_ts


def redact_entry(entry: dict, rules: dict, unknown_policy: str, redact_types: list[str]) -> tuple[dict, list[str]]:
    """Return (cleaned entry, list of what changed)."""
    out = dict(entry)
    changes: list[str] = []

    ts = entry.get('ts')
    if ts and normalize_ts(ts) != ts:
        out['ts'] = normalize_ts(ts)
        changes.append('timestamp')

    if entry.get('source') == 'git' or entry.get('marker'):
        return out, changes

    kind = entry.get('type')
    if kind == 'browser' and not entry.get('excluded'):
        url = entry.get('url', '')
        domain = entry.get('domain') or domain_of(url)
        if domain:
            site_class = classify_domain(domain, rules)
            out['domain'] = domain
            out['site_class'] = site_class
            if not (entry.get('domain') and entry.get('site_class')):
                changes.append('domain')
            if keeps_detail(site_class, unknown_policy):
                stripped = strip_url(url)
                if url and stripped != url:
                    out['url'] = stripped
                    changes.append('url query')
            else:
                for key in ('url', 'tab_title'):
                    if out.pop(key, None):
                        changes.append(f'{key} removed')
        if out.get('window'):
            out['window'] = ''
            changes.append('window removed')
    elif kind in redact_types and out.get('window'):
        out['window'] = ''
        changes.append('window removed')

    return out, changes


def process_file(path: Path, apply: bool, rules: dict, unknown_policy: str,
                 redact_types: list[str]) -> dict[str, int]:
    stats: dict[str, int] = {}
    out_lines: list[str] = []
    changed_any = False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            out_lines.append(line)  # never drop what we can't read
            continue
        cleaned, changes = redact_entry(entry, rules, unknown_policy, redact_types)
        for c in changes:
            stats[c] = stats.get(c, 0) + 1
        if changes:
            changed_any = True
            out_lines.append(json.dumps(cleaned))
        else:
            out_lines.append(line)

    if apply and changed_any:
        backup = path.with_name(path.name + '.bak')
        if backup.exists():
            backup = path.with_name(f'{path.name}.{datetime.now():%Y%m%d%H%M%S}.bak')
        shutil.copy2(path, backup)
        rewrite_atomic(path, '\n'.join(out_lines) + '\n')
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Apply the current privacy rules to existing logs')
    parser.add_argument('--dir', type=Path, default=Path(Config.LOGS_DIR), help='Log directory')
    parser.add_argument('--apply', action='store_true', help='Rewrite the files (default is a dry run)')
    args = parser.parse_args(argv)

    files = sorted(args.dir.glob('*.jsonl'))
    if not files:
        print(f'No log files in {args.dir}')
        return 0

    total: dict[str, int] = {}
    for f in files:
        stats = process_file(f, args.apply, Config.BROWSER_RULES, Config.BROWSER_UNKNOWN,
                             Config.REDACT_TITLE_TYPES)
        if stats:
            print(f'{f.name}: ' + ', '.join(f'{k} ×{v}' for k, v in sorted(stats.items())))
        for k, v in stats.items():
            total[k] = total.get(k, 0) + v

    if not total:
        print('Nothing to change.')
    else:
        summary = ', '.join(f'{k} ×{v}' for k, v in sorted(total.items()))
        verb = 'Changed' if args.apply else 'Would change'
        print(f'\n{verb}: {summary}')
        if not args.apply:
            print('Dry run — re-run with --apply to rewrite the files (a .bak copy of each is kept).')
    return 0


if __name__ == '__main__':
    sys.exit(main())

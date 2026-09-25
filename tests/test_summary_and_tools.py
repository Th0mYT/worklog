import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from config import Config
from logger import redact_logs
from logger.git_enricher import _parse_commit_lines, _parse_numstat
from summarizer import daily_summary as ds
from summarizer.sessions import build_day

RULES = {'work': ['github.com'], 'personal': ['youtube.com']}
D = '2026-09-24'
COMMESSE = [{'name': 'Screening', 'client': 'DDH', 'keywords': ['KH-682']}]


def snap(hhmm, app='PyCharm', typ='coding', **extra):
    return {'ts': f'{D}T{hhmm}:00', 'app': app, 'window': '', 'type': typ, **extra}


def entries():
    return [snap(f'09:{m:02d}', project='api', file='a.py', branch='feature/KH-682-x') for m in range(0, 30, 5)] + [
        {'ts': f'{D}T09:20:00', 'source': 'git', 'repo': 'api', 'type': 'coding',
         'message': 'feat(api): add endpoint', 'branch': 'feature/KH-682-x'},
        snap('09:30', app='Slack', typ='communication'),
        snap('09:35', app='Slack', typ='communication'),
        {'ts': f'{D}T09:40:00', 'marker': 'stop'},
    ]


def day_for(commesse):
    return build_day(entries(), commesse=commesse, rules=RULES, unknown_policy='hide', heartbeat=300)


class Parsing(unittest.TestCase):
    def test_description_lines(self):
        reply = """Here you go:
b1: Implemented the endpoint (1.5h)
- **b2**: Reviewed PRs
[b3] - Standup
b9: unknown block
B4. Cleanup"""
        got = ds.parse_descriptions(reply, {'b1', 'b2', 'b3', 'b4'})
        self.assertEqual(got, {'b1': 'Implemented the endpoint', 'b2': 'Reviewed PRs',
                               'b3': 'Standup', 'b4': 'Cleanup'})

    def test_first_description_for_an_id_wins(self):
        self.assertEqual(ds.parse_descriptions('b1: one\nb1: two', {'b1'}), {'b1': 'one'})


class Rendering(unittest.TestCase):
    def test_commesse_mode_has_headers_durations_and_total(self):
        with mock.patch.object(Config, 'COMMESSE', COMMESSE):
            day = day_for(COMMESSE)
            text = ds.render_summary(day, {'b1': 'Built the endpoint'})
        lines = text.splitlines()
        self.assertEqual(lines[0], '## Screening')
        self.assertRegex(lines[1], r'^Built the endpoint \(\d+(\.\d+)?h\) \[api\]$')
        self.assertIn('## Non assegnata', lines)
        self.assertRegex(lines[-1], r'^Total: \d+(\.\d+)?h$')
        listed = sum(float(l.rsplit('(', 1)[1].split('h')[0]) for l in lines if l.endswith(']') or l.endswith('h)'))
        self.assertAlmostEqual(listed, day.total_hours)

    def test_missing_description_falls_back_to_the_data(self):
        with mock.patch.object(Config, 'COMMESSE', COMMESSE):
            text = ds.render_summary(day_for(COMMESSE), {})
        self.assertIn('feat(api): add endpoint', text)

    def test_plain_mode_has_no_headers(self):
        with mock.patch.object(Config, 'COMMESSE', []):
            text = ds.render_summary(day_for([]), {})
        self.assertFalse(any(l.startswith('##') for l in text.splitlines()))

    def test_output_matches_what_the_ui_parses(self):
        # ui/app.py: /^(.+?)\s+[(]([^)]+)[)](?:\s+\[([^\]]*)\])?$/
        import re
        rx = re.compile(r'^(.+?)\s+[(]([^)]+)[)](?:\s+\[([^\]]*)\])?$')
        with mock.patch.object(Config, 'COMMESSE', COMMESSE):
            text = ds.render_summary(day_for(COMMESSE), {'b1': 'Fix (really) the thing'})
        for line in text.splitlines():
            if line and not line.startswith('##') and not line.startswith('Total'):
                self.assertTrue(rx.match(line), line)


class PromptContent(unittest.TestCase):
    def test_prompt_lists_blocks_with_evidence_and_no_private_data(self):
        day = day_for(COMMESSE)
        prompt = ds.build_prompt(date(2026, 9, 24), day)
        self.assertIn('[b1]', prompt)
        self.assertIn('assignment: Screening', prompt)
        self.assertIn('feat(api): add endpoint', prompt)
        self.assertIn('feature/KH-682-x', prompt)
        self.assertIn('api › a.py', prompt)

    def test_prompt_never_contains_personal_or_unclassified_browsing(self):
        data = entries() + [
            {'ts': f'{D}T09:36:00', 'app': 'Google Chrome', 'type': 'browser', 'window': '',
             'url': 'https://www.youtube.com/watch?v=secret', 'tab_title': 'Competitor secret video'},
            {'ts': f'{D}T09:37:00', 'app': 'Google Chrome', 'type': 'browser', 'window': '',
             'domain': 'rival.example', 'site_class': 'unknown'},
            {'ts': f'{D}T09:38:00', 'app': 'Google Chrome', 'type': 'browser', 'window': '',
             'excluded': True, 'reason': 'private'},
        ]
        data.sort(key=lambda e: e['ts'])
        day = build_day(data, commesse=COMMESSE, rules=RULES, unknown_policy='hide', heartbeat=300)
        prompt = ds.build_prompt(date(2026, 9, 24), day)
        for leaked in ('secret', 'Competitor', 'rival', 'youtube'):
            self.assertNotIn(leaked, prompt)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)
        (self.logs / f'{D}.jsonl').write_text('\n'.join(json.dumps(e) for e in entries()) + '\n')
        self.out = Path(self.tmp.name) / 'summaries'

    def tearDown(self):
        self.tmp.cleanup()

    def run_summary(self, **kw):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf), \
             mock.patch.object(Config, 'COMMESSE', COMMESSE), \
             mock.patch.object(Config, 'BROWSER_RULES', RULES), \
             mock.patch.object(Config, 'SUMMARIES_DIR', str(self.out)):
            ok = ds.summarize(target=date(2026, 9, 24), log_dir=self.logs, **kw)
        return ok, buf.getvalue()

    def test_no_llm_prints_and_saves_nothing(self):
        ok, out = self.run_summary(no_llm=True)
        self.assertTrue(ok)
        self.assertIn('## Screening', out)
        self.assertFalse(self.out.exists())

    def test_llm_reply_is_merged_and_saved(self):
        with mock.patch.object(ds, '_call_ollama', return_value='b1: Built the endpoint\nb2: Team chat'):
            ok, out = self.run_summary(backend='ollama')
        self.assertTrue(ok)
        saved = (self.out / f'{D}.md').read_text()
        self.assertIn('Built the endpoint', saved)
        self.assertRegex(saved.splitlines()[-1], r'^Total: ')

    def test_garbage_reply_still_produces_a_summary(self):
        with mock.patch.object(ds, '_call_ollama', return_value='I cannot help with that'):
            ok, out = self.run_summary(backend='ollama')
        self.assertTrue(ok)
        self.assertIn('WARNING', out)
        self.assertIn('feat(api): add endpoint', (self.out / f'{D}.md').read_text())


class GitParsing(unittest.TestCase):
    def test_commit_lines(self):
        raw = '\x1f'.join(['abc123', '2026-09-24T09:40:54+02:00', 'refs/heads/feature/x', 'feat: a | b'])
        [c] = _parse_commit_lines(raw + '\n' + 'garbage line')
        self.assertEqual((c['hash'], c['branch'], c['message']), ('abc123', 'feature/x', 'feat: a | b'))
        self.assertRegex(c['ts'], r'^2026-09-24T\d\d:\d\d:54$')

    def test_numstat(self):
        raw = '3\t1\tsrc/a.py\n-\t-\timg/logo.png\n10\t0\tb.py\n'
        self.assertEqual(_parse_numstat(raw), (3, 13, 1, ['src/a.py', 'img/logo.png', 'b.py']))
        self.assertEqual(_parse_numstat(''), (0, 0, 0, []))


class Redaction(unittest.TestCase):
    RULES = {'work': ['github.com'], 'personal': ['youtube.com']}

    def clean(self, entry):
        return redact_logs.redact_entry(entry, self.RULES, 'hide', ['communication'])

    def test_personal_page_loses_title_and_url(self):
        e, changes = self.clean({'ts': f'{D}T09:00:00', 'type': 'browser', 'app': 'Chrome',
                                 'url': 'https://youtube.com/watch?v=1', 'tab_title': 'Video'})
        self.assertEqual((e['domain'], e['site_class']), ('youtube.com', 'personal'))
        self.assertNotIn('url', e)
        self.assertNotIn('tab_title', e)
        self.assertTrue(changes)

    def test_work_page_keeps_title_but_loses_query(self):
        e, _ = self.clean({'ts': f'{D}T09:00:00', 'type': 'browser', 'app': 'Chrome',
                           'url': 'https://github.com/a/b?token=abc', 'tab_title': 'Repo'})
        self.assertEqual((e['url'], e['tab_title']), ('https://github.com/a/b', 'Repo'))

    def test_chat_window_and_git_timestamp(self):
        e, _ = self.clean({'ts': f'{D}T09:00:00', 'type': 'communication', 'app': 'Slack', 'window': 'DM with X'})
        self.assertEqual(e['window'], '')
        g, changes = self.clean({'ts': f'{D} 09:40:54 +0200', 'source': 'git', 'repo': 'r', 'message': 'm'})
        self.assertNotIn(' ', g['ts'])
        self.assertEqual(changes, ['timestamp'])

    def test_idempotent_and_backup_on_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / f'{D}.jsonl'
            lines = [
                json.dumps({'ts': f'{D}T09:00:00', 'type': 'browser', 'app': 'Chrome',
                            'url': 'https://youtube.com/x?y=1', 'tab_title': 'V'}),
                'this line is not json',
            ]
            f.write_text('\n'.join(lines) + '\n')
            args = (self.RULES, 'hide', ['communication'])
            self.assertTrue(redact_logs.process_file(f, False, *args))
            self.assertEqual(f.read_text().splitlines()[0], lines[0])          # dry run leaves it alone
            redact_logs.process_file(f, True, *args)
            self.assertTrue((Path(tmp) / f'{D}.jsonl.bak').exists())
            self.assertEqual(f.read_text().splitlines()[1], 'this line is not json')  # unreadable lines survive
            self.assertNotIn('youtube.com/x', f.read_text().splitlines()[0])
            self.assertEqual(redact_logs.process_file(f, True, *args), {})      # second run: nothing left


if __name__ == '__main__':
    unittest.main()

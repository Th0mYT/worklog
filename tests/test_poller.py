import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from config import Config
from logger import activity_poller as ap
from logger import pause
from logger.browser_reader import TabInfo
from logger.extractors import RepoInfo
from logger.window_info import FrontWindow

RULES = {'work': ['github.com', 'localhost'], 'personal': ['youtube.com']}


def patch_config(**values):
    defaults = dict(BROWSER_RULES=RULES, BROWSER_UNKNOWN='hide', IGNORE_APPS=['worklog'],
                    REDACT_TITLE_TYPES=['communication'], POLL_INTERVAL=300, SAMPLE_INTERVAL=20)
    defaults.update(values)
    return mock.patch.multiple(Config, **defaults)


def tab(title, url, private=False):
    return lambda app: TabInfo(title, url, private)


INDEX = {'khero-backend': RepoInfo('khero-backend', '/nonexistent/khero-backend', ['ddh', 'backend'])}


class BuildEntry(unittest.TestCase):
    def build(self, app, window='', tab_fn=None, **kw):
        with patch_config():
            return ap.build_entry(FrontWindow(app=app, window=window), kw.get('index', {}),
                                  tab_fn=tab_fn or (lambda a: None), cwd_fn=kw.get('cwd_fn', lambda a: ''))

    def test_private_window_records_nothing_else(self):
        e = self.build('Google Chrome', 'Secret video - Google Chrome',
                       tab('Secret video', 'https://competitor.com/watch?v=1', private=True))
        self.assertEqual(e, {'app': 'Google Chrome', 'window': '', 'type': 'browser',
                             'excluded': True, 'reason': 'private'})

    def test_work_domain_keeps_title_and_query_less_url(self):
        e = self.build('Google Chrome', 'x', tab('PR #12', 'https://github.com/o/r/pull/12?tab=files#diff'))
        self.assertEqual((e['domain'], e['site_class'], e['tab_title'], e['url']),
                         ('github.com', 'work', 'PR #12', 'https://github.com/o/r/pull/12'))
        self.assertEqual(e['window'], '')

    def test_personal_and_unknown_domains_keep_only_the_domain(self):
        for url, cls in (('https://youtube.com/watch?v=1', 'personal'), ('https://competitor.com/x', 'unknown')):
            e = self.build('Google Chrome', 'x', tab('Some title', url))
            self.assertEqual(e['site_class'], cls)
            self.assertNotIn('tab_title', e)
            self.assertNotIn('url', e)
            self.assertEqual(e['window'], '')

    def test_unknown_policy_work_keeps_detail(self):
        with patch_config(BROWSER_UNKNOWN='work'):
            e = ap.build_entry(FrontWindow(app='Google Chrome'), {}, tab_fn=tab('T', 'https://intranet.corp/a?q=1'))
        self.assertEqual((e['tab_title'], e['url']), ('T', 'https://intranet.corp/a'))

    def test_browser_without_tab_info(self):
        e = self.build('Firefox', 'Some page — Mozilla Firefox')
        self.assertEqual(e, {'app': 'Firefox', 'window': '', 'type': 'browser'})

    def test_ignored_app_is_excluded_not_skipped(self):
        e = self.build('worklog')
        self.assertTrue(e['excluded'])
        self.assertEqual(e['reason'], 'ignored-app')
        self.assertIsNone(self.build(''))

    def test_communication_title_is_redacted(self):
        e = self.build('Slack', 'Mario Rossi (DM) - Acme - Slack')
        self.assertEqual((e['type'], e['window']), ('communication', ''))

    def test_meeting_keeps_title(self):
        e = self.build('Zoom', 'Zoom Meeting - Sprint planning')
        self.assertEqual(e['type'], 'meeting')
        self.assertIn('Sprint planning', e['window'])

    def test_coding_entry_gets_project_file_tags_branch(self):
        with mock.patch.object(ap, 'current_branch', return_value='feature/KH-682-x'), \
             mock.patch.object(ap, 'wip_files', return_value=['a.py']):
            with patch_config():
                e = ap.build_entry(FrontWindow(app='WebStorm', window='khero-backend – users.service.ts'),
                                   INDEX, with_wip=True, tab_fn=lambda a: None, cwd_fn=lambda a: '')
        self.assertEqual(e['project'], 'khero-backend')
        self.assertEqual(e['file'], 'users.service.ts')
        self.assertEqual(e['tags'], ['ddh', 'backend'])
        self.assertEqual(e['branch'], 'feature/KH-682-x')
        self.assertEqual(e['wip_files'], ['a.py'])

    def test_wip_only_when_requested(self):
        with mock.patch.object(ap, 'current_branch', return_value=''), \
             mock.patch.object(ap, 'wip_files', return_value=['a.py']):
            with patch_config():
                e = ap.build_entry(FrontWindow(app='WebStorm', window='khero-backend – x.ts'), INDEX,
                                   tab_fn=lambda a: None, cwd_fn=lambda a: '')
        self.assertNotIn('wip_files', e)

    def test_terminal_uses_cwd_to_find_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'khero-backend'
            (repo / '.git').mkdir(parents=True)
            with mock.patch.object(ap, 'current_branch', return_value=''), patch_config():
                e = ap.build_entry(FrontWindow(app='Terminal', window='zsh'), INDEX,
                                   tab_fn=lambda a: None, cwd_fn=lambda a: str(repo / 'src'))
        self.assertEqual(e['project'], 'khero-backend')
        self.assertEqual(e['tags'], ['ddh', 'backend'])


class TrackerBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.now = datetime(2026, 9, 24, 9, 0, 0)
        self.front = FrontWindow(app='PyCharm', window='worklog – a.py')
        self.paused = False
        self.cfg = patch_config()
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        self.tmp.cleanup()

    def tracker(self):
        return ap.Tracker(self.dir, front_fn=lambda: self.front, now_fn=lambda: self.now,
                          entry_fn=lambda f, i, with_wip=False: {'app': f.app, 'window': f.window, 'type': 'coding'},
                          paused_fn=lambda: self.paused)

    def lines(self):
        f = self.dir / '2026-09-24.jsonl'
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def test_writes_on_change_and_heartbeat_only(self):
        t = self.tracker()
        self.assertTrue(t.tick())
        self.now += timedelta(seconds=20)
        self.assertFalse(t.tick())                       # unchanged, heartbeat not due
        self.front = FrontWindow(app='PyCharm', window='worklog – b.py')
        self.now += timedelta(seconds=20)
        self.assertTrue(t.tick())                        # window changed
        self.now += timedelta(seconds=299)
        self.assertFalse(t.tick())                       # 299 s after last write
        self.now += timedelta(seconds=1)
        self.assertTrue(t.tick())                        # heartbeat
        self.assertEqual(len(self.lines()), 3)

    def test_pause_blocks_writes_and_marks_span(self):
        t = self.tracker()
        t.tick()
        self.paused = True
        self.now += timedelta(seconds=400)
        self.assertFalse(t.tick())                       # heartbeat due, but paused wins
        t.enter_pause('2026-09-24T09:03:00')
        self.paused = False
        self.now += timedelta(seconds=60)
        t.leave_pause()
        self.assertTrue(t.tick())                        # written immediately after resume
        kinds = [(l.get('marker'), l['ts']) for l in self.lines()]
        self.assertEqual([k for k, _ in kinds], [None, 'pause', 'resume', None])
        self.assertEqual(kinds[1][1], '2026-09-24T09:03:00')

    def test_idle_marker_is_backdated_to_last_input(self):
        t = self.tracker()
        t.tick()
        self.now += timedelta(seconds=310)
        t.enter_idle(310)
        t.enter_idle(320)                                # only once per idle episode
        t.leave_idle()
        markers = [l for l in self.lines() if l.get('marker')]
        self.assertEqual([m['marker'] for m in markers], ['idle_start', 'idle_end'])
        self.assertEqual(markers[0]['ts'], '2026-09-24T09:00:00')

    def test_close_writes_stop_marker_backdated_by_idle(self):
        t = self.tracker()
        t.tick()
        self.now += timedelta(seconds=500)
        t.close(idle_seconds=400)
        stop = self.lines()[-1]
        self.assertEqual((stop['marker'], stop['ts']), ('stop', '2026-09-24T09:01:40'))

    def test_close_after_idle_does_not_double_mark(self):
        t = self.tracker()
        t.enter_idle(300)
        t.close()
        self.assertEqual([l['marker'] for l in self.lines()], ['idle_start'])


class PauseFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(pause, 'PAUSE_FILE', Path(self.tmp.name) / 'paused.json')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_lifecycle(self):
        now = datetime(2026, 9, 24, 10, 0, 0)
        self.assertFalse(pause.is_paused(now))
        state = pause.pause(30, now)
        self.assertTrue(state['paused'])
        self.assertTrue(pause.is_paused(now + timedelta(minutes=29)))
        self.assertFalse(pause.is_paused(now + timedelta(minutes=31)))   # expired
        pause.pause(0, now)
        self.assertTrue(pause.is_paused(now + timedelta(days=3)))        # until resumed
        self.assertFalse(pause.resume()['paused'])

    def test_corrupt_file_means_not_paused(self):
        pause.PAUSE_FILE.write_text('{not json')
        self.assertFalse(pause.is_paused())


if __name__ == '__main__':
    unittest.main()

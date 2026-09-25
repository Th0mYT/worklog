import json
import tempfile
import tomllib
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from config import Config
from logger import pause

import ui.app as app

D = '2026-09-24'


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.logs = root / 'logs'
        self.logs.mkdir()
        self.cfg_path = root / 'config.toml'   # does not exist yet → the API won't auto-start the poller
        self._saved = {k: getattr(Config, k) for k in vars(Config) if k.isupper()}
        Config.LOGS_DIR = str(self.logs)
        Config.BROWSER_RULES = {'work': ['github.com'], 'personal': ['youtube.com']}
        self.patches = [
            mock.patch.object(app, '_CONFIG_PATH', self.cfg_path),
            mock.patch.object(pause, 'PAUSE_FILE', root / 'paused.json'),
        ]
        for p in self.patches:
            p.start()
        self.api = app._API()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for k, v in self._saved.items():
            setattr(Config, k, v)
        self.tmp.cleanup()

    def write_log(self, entries, day=D):
        (self.logs / f'{day}.jsonl').write_text('\n'.join(json.dumps(e) for e in entries) + '\n')

    def read_log(self, day=D):
        return [json.loads(l) for l in (self.logs / f'{day}.jsonl').read_text().splitlines()]

    # -- settings -------------------------------------------------------------------

    def test_settings_roundtrip_keeps_new_keys_and_unmanaged_ones(self):
        self.cfg_path.write_text('summaries_dir = "/tmp/sums"\nanthropic_api_key = "sk-keep"\nanthropic_model = "m"\n')
        settings = self.api.get_settings()
        settings.update(sample_interval=45, ignore_apps=['worklog', 'Spotify'], browser_unknown='work',
                        browser_rules={'work': ['Example.com', 'https://www.corp.dev/x'], 'personal': ['twitch.tv']},
                        redact_title_types=['communication', 'other'])
        self.assertTrue(self.api.save_settings(settings)['ok'])

        parsed = tomllib.loads(self.cfg_path.read_text())
        self.assertEqual(parsed['sample_interval'], 45)
        self.assertEqual(parsed['ignore_apps'], ['worklog', 'Spotify'])
        self.assertEqual(parsed['browser_unknown'], 'work')
        self.assertEqual(parsed['browser_rules'], {'work': ['example.com', 'corp.dev'], 'personal': ['twitch.tv']})
        self.assertEqual(parsed['redact_title_types'], ['communication', 'other'])
        # keys without a field in the UI survive the save
        self.assertEqual((parsed['summaries_dir'], parsed['anthropic_api_key']), ('/tmp/sums', 'sk-keep'))
        self.assertEqual(Config.SAMPLE_INTERVAL, 45)
        self.assertEqual(Config.BROWSER_RULES['work'], ['example.com', 'corp.dev'])

    def test_sample_interval_has_a_floor(self):
        settings = self.api.get_settings()
        settings['sample_interval'] = 1
        self.api.save_settings(settings)
        self.assertEqual(Config.SAMPLE_INTERVAL, 5)

    # -- entries --------------------------------------------------------------------

    def test_mark_personal_and_restore(self):
        self.write_log([{'ts': f'{D}T09:00:00', 'app': 'Chrome', 'type': 'browser'},
                        {'ts': f'{D}T09:00:00', 'marker': 'start'}])
        self.assertTrue(self.api.set_entry_excluded(D, f'{D}T09:00:00', True)['ok'])
        entries = self.read_log()
        self.assertTrue(entries[0]['excluded'])
        self.assertEqual(entries[0]['reason'], 'marked-personal')
        self.assertNotIn('excluded', entries[1])          # the marker with the same ts is left alone
        self.assertTrue(self.api.set_entry_excluded(D, f'{D}T09:00:00', False)['ok'])
        self.assertNotIn('excluded', self.read_log()[0])
        self.assertNotIn('reason', self.read_log()[0])
        self.assertFalse(self.api.set_entry_excluded(D, 'nope', True)['ok'])

    def test_commit_and_snapshot_in_the_same_second_are_told_apart(self):
        ts = f'{D}T09:00:00'
        self.write_log([{'ts': ts, 'app': 'x', 'type': 'coding'},
                        {'ts': ts, 'source': 'git', 'repo': 'r', 'message': 'm'}])
        self.assertTrue(self.api.set_entry_excluded(D, ts, True, True)['ok'])
        snapshot, commit = self.read_log()
        self.assertNotIn('excluded', snapshot)
        self.assertTrue(commit['excluded'])
        self.assertTrue(self.api.delete_entry(D, ts, True)['ok'])
        self.assertEqual([e.get('app') for e in self.read_log()], ['x'])

    def test_rewrite_does_not_leave_temp_files(self):
        self.write_log([{'ts': f'{D}T09:00:00', 'app': 'x', 'type': 'coding'}])
        self.api.set_entry_tags(D, f'{D}T09:00:00', 'a')
        self.assertEqual([p.name for p in self.logs.iterdir()], [f'{D}.jsonl'])

    def test_classifying_a_domain_does_not_copy_env_api_keys_to_disk(self):
        Config.OPENAI_API_KEY = 'sk-from-env'
        self.assertTrue(self.api.classify_domain_rule('example.com', 'work')['ok'])
        self.assertNotIn('sk-from-env', self.cfg_path.read_text())
        self.assertEqual(Config.OPENAI_API_KEY, 'sk-from-env')     # still usable in this session

    def test_tags_still_work_after_refactor(self):
        self.write_log([{'ts': f'{D}T09:00:00', 'app': 'x', 'type': 'coding'}])
        r = self.api.set_entry_tags(D, f'{D}T09:00:00', 'a, b, A')
        self.assertEqual(r, {'ok': True, 'tags': ['a', 'b']})
        self.assertEqual(self.read_log()[0]['tags'], ['a', 'b'])
        self.api.set_entry_tags(D, f'{D}T09:00:00', '')
        self.assertNotIn('tags', self.read_log()[0])

    def test_log_entries_sorted_and_site_class_reflects_current_rules(self):
        self.write_log([
            {'ts': f'{D} 09:40:54 +0200', 'source': 'git', 'repo': 'r', 'message': 'm'},
            {'ts': f'{D}T09:14:15', 'app': 'Chrome', 'type': 'browser', 'url': 'https://www.youtube.com/x'},
            {'ts': f'{D}T09:50:00', 'app': 'Chrome', 'type': 'browser', 'domain': 'intranet.corp', 'site_class': 'unknown'},
        ])
        entries = self.api.log_entries(D)['entries']
        self.assertEqual(entries[0]['app'], 'Chrome')                 # 09:14 first, not the commit
        self.assertEqual((entries[0]['domain'], entries[0]['site_class']), ('youtube.com', 'personal'))
        self.api.classify_domain_rule('intranet.corp', 'work')
        self.assertEqual(self.api.log_entries(D)['entries'][-1]['site_class'], 'work')

    def test_classify_moves_a_domain_between_lists(self):
        self.assertTrue(self.api.classify_domain_rule('https://www.YouTube.com/x', 'work')['ok'])
        self.assertIn('youtube.com', Config.BROWSER_RULES['work'])
        self.assertNotIn('youtube.com', Config.BROWSER_RULES['personal'])
        self.api.classify_domain_rule('youtube.com', '')
        self.assertNotIn('youtube.com', Config.BROWSER_RULES['work'])
        self.assertFalse(self.api.classify_domain_rule('', 'work')['ok'])

    def test_unclassified_domains(self):
        self.write_log([
            {'ts': f'{D}T09:00:00', 'type': 'browser', 'domain': 'rival.example'},
            {'ts': f'{D}T09:05:00', 'type': 'browser', 'url': 'https://rival.example/a?b=1'},
            {'ts': f'{D}T09:10:00', 'type': 'browser', 'domain': 'github.com'},
            {'ts': f'{D}T09:15:00', 'type': 'browser', 'excluded': True, 'reason': 'private'},
            {'ts': f'{D}T09:20:00', 'type': 'browser', 'domain': 'other.example'},
        ])
        self.assertEqual(self.api.unclassified_domains(7)['domains'],
                         [{'domain': 'rival.example', 'count': 2}, {'domain': 'other.example', 'count': 1}])

    # -- listing / status -------------------------------------------------------------

    def test_markers_are_not_counted_as_entries(self):
        today = datetime.now().strftime('%Y-%m-%d')
        self.write_log([{'ts': f'{today}T09:00:00', 'marker': 'start'},
                        {'ts': f'{today}T09:01:00', 'app': 'x', 'type': 'coding'},
                        {'ts': f'{today} 09:30:00 +0000', 'source': 'git', 'repo': 'r', 'message': 'm'},
                        {'ts': f'{today}T17:00:00', 'marker': 'stop'}], day=today)
        listing = self.api.logs()['logs'][0]
        self.assertEqual((listing['count'], listing['git_count']), (2, 1))
        self.assertNotIn('other', listing['types'])
        last_ts, count = self.api._today_stats()
        self.assertEqual(count, 2)
        self.assertNotEqual(last_ts, f'{today}T17:00:00')

    def test_pause_shows_up_in_status(self):
        self.assertFalse(self.api.status()['paused'])
        self.assertTrue(self.api.pause_tracking(30)['paused'])
        st = self.api.status()
        self.assertTrue(st['paused'])
        self.assertTrue(st['paused_until'])
        self.assertFalse(self.api.resume_tracking()['paused'])
        self.assertIn('capture_problem', self.api.status())


if __name__ == '__main__':
    unittest.main()

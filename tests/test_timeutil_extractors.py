import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from logger.browser_reader import parse_tab_output
from logger.extractors import (
    build_repo_index, classify_domain, domain_of, find_repo_root, keeps_detail, match_repo,
    parse_ide_title, parse_lsof_cwd, path_from_title, pick_foreground_pid, strip_url,
)
from logger.git_info import current_branch
from logger.timeutil import entry_time, normalize_ts, parse_ts


class TimeUtil(unittest.TestCase):
    def test_naive_formats(self):
        self.assertEqual(parse_ts('2026-09-24T09:14:15'), datetime(2026, 9, 24, 9, 14, 15))
        self.assertEqual(parse_ts('2026-09-24 09:14'), datetime(2026, 9, 24, 9, 14))

    def test_offset_is_converted_to_local_naive(self):
        expected = datetime(2026, 9, 24, 7, 40, 54, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        self.assertEqual(parse_ts('2026-09-24 09:40:54 +0200'), expected)
        self.assertEqual(parse_ts('2026-09-24T09:40:54+02:00'), expected)
        self.assertEqual(parse_ts('2026-09-24T07:40:54Z'), expected)

    def test_invalid(self):
        for bad in ('', None, 'nope', '2026-13-45T00:00:00'):
            self.assertIsNone(parse_ts(bad))
        self.assertEqual(normalize_ts('nope'), 'nope')

    def test_git_entry_sorts_between_snapshots(self):
        # regression: ' ' < 'T' used to put every commit before every snapshot
        entries = [
            {'ts': '2026-09-24T09:14:15'},
            {'ts': '2026-09-24T10:04:20'},
        ]
        commit = {'ts': '2026-09-24T09:40:54'}
        ordered = sorted(entries + [commit], key=entry_time)
        self.assertEqual(ordered[1], commit)


class IdeTitles(unittest.TestCase):
    def test_jetbrains(self):
        self.assertEqual(parse_ide_title('PyCharm', 'worklog – activity_poller.py'), ('worklog', 'activity_poller.py'))
        self.assertEqual(parse_ide_title('pycharm', 'worklog'), ('worklog', ''))
        self.assertEqual(
            parse_ide_title('WebStorm', 'khero-angular [~/Lavoro/khero-angular] – …/app.component.ts'),
            ('khero-angular', 'app.component.ts'))

    def test_vscode_like(self):
        self.assertEqual(parse_ide_title('Cursor', '● foo.ts — my-app'), ('my-app', 'foo.ts'))
        self.assertEqual(parse_ide_title('Visual Studio Code', 'main.py — proj [Extension Development Host]'),
                         ('proj', 'main.py'))
        self.assertEqual(parse_ide_title('Visual Studio Code', 'Welcome'), ('Welcome', ''))

    def test_xcode_and_unknown(self):
        self.assertEqual(parse_ide_title('Xcode', 'MyApp — AppDelegate.swift'), ('MyApp', 'AppDelegate.swift'))
        self.assertEqual(parse_ide_title('Terminal', 'zsh — 80x24'), ('', ''))
        self.assertEqual(parse_ide_title('PyCharm', ''), ('', ''))


class Urls(unittest.TestCase):
    def test_domain_and_strip(self):
        self.assertEqual(domain_of('https://www.Example.com/a?b=1'), 'example.com')
        self.assertEqual(domain_of('http://192.168.1.2:9443/#!/auth'), '192.168.1.2')
        self.assertEqual(domain_of(''), '')
        self.assertEqual(strip_url('https://user:pw@gist.github.com:8443/a/b?q=1#f'), 'https://gist.github.com:8443/a/b')
        self.assertEqual(strip_url('not a url'), '')

    def test_classification(self):
        rules = {'work': ['github.com', 'localhost'], 'personal': ['youtube.com', 'studio.youtube.com']}
        self.assertEqual(classify_domain('gist.github.com', rules), 'work')
        self.assertEqual(classify_domain('notgithub.com', rules), 'unknown')
        self.assertEqual(classify_domain('youtube.com', rules), 'personal')
        self.assertEqual(classify_domain('', rules), 'unknown')
        # most specific rule wins
        rules['work'].append('studio.youtube.com')
        rules['personal'] = ['youtube.com']
        self.assertEqual(classify_domain('studio.youtube.com', rules), 'work')

    def test_keeps_detail(self):
        self.assertTrue(keeps_detail('work', 'hide'))
        self.assertFalse(keeps_detail('unknown', 'hide'))
        self.assertTrue(keeps_detail('unknown', 'work'))
        self.assertFalse(keeps_detail('personal', 'work'))

    def test_tab_output(self):
        tab = parse_tab_output('My page|||https://a.com/x|||incognito\n')
        self.assertTrue(tab.private)
        self.assertEqual((tab.title, tab.url), ('My page', 'https://a.com/x'))
        self.assertFalse(parse_tab_output('T|||https://a.com|||normal').private)
        self.assertFalse(parse_tab_output('T|||').private)
        self.assertIsNone(parse_tab_output(''))


class RepoIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('api', 'api-gateway', 'web'):
            (self.root / 'ws' / name / '.git').mkdir(parents=True)
        (self.root / 'solo' / '.git').mkdir(parents=True)
        (self.root / 'plain').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_and_match(self):
        solo = str(self.root / 'solo')
        ws = str(self.root / 'ws')
        index = build_repo_index([solo], [ws], {solo: ['x'], ws: ['team']})
        self.assertEqual(index['solo'].tags, ['x'])
        self.assertEqual(index['web'].tags, ['team'])
        self.assertEqual(match_repo(index, 'api').name, 'api')
        # longest name wins when matching inside a title
        self.assertEqual(match_repo(index, '', 'api-gateway – main.py').name, 'api-gateway')
        self.assertIsNone(match_repo(index, 'nothing', 'nothing here'))

    def test_find_repo_root_and_branch(self):
        deep = self.root / 'solo' / 'src' / 'pkg'
        deep.mkdir(parents=True)
        self.assertEqual(find_repo_root(str(deep)), str(self.root / 'solo'))
        self.assertEqual(find_repo_root(str(self.root / 'plain')), '')
        self.assertEqual(find_repo_root(''), '')
        (self.root / 'solo' / '.git' / 'HEAD').write_text('ref: refs/heads/feature/KH-682-x\n')
        self.assertEqual(current_branch(str(self.root / 'solo')), 'feature/KH-682-x')
        (self.root / 'solo' / '.git' / 'HEAD').write_text('0123456789abcdef\n')
        self.assertEqual(current_branch(str(self.root / 'solo')), '')


class TerminalParsing(unittest.TestCase):
    def test_paths_and_ps(self):
        self.assertEqual(path_from_title('user@host: ~/Lavoro/worklog'), '~/Lavoro/worklog')
        self.assertEqual(path_from_title('zsh'), '')
        ps = '  100 Ss   -zsh\n  200 S+   vim\n  250 S+   node\n'
        self.assertEqual(pick_foreground_pid(ps), 250)
        self.assertEqual(pick_foreground_pid('  100 Ss -zsh\n'), 0)
        self.assertEqual(parse_lsof_cwd('p123\nfcwd\nn/Users/me/proj\n'), '/Users/me/proj')


if __name__ == '__main__':
    unittest.main()


class ReviewRegressions(unittest.TestCase):
    def test_repo_names_match_whole_tokens_only(self):
        from logger.extractors import RepoInfo, match_repo
        idx = {'app': RepoInfo('app', '/x/app'), 'api': RepoInfo('api', '/x/api')}
        self.assertIsNone(match_repo(idx, '', 'Slack | happy-channel'))
        self.assertIsNone(match_repo(idx, '', 'Rapid notes'))
        self.assertEqual(match_repo(idx, '', 'api – main.py').name, 'api')
        self.assertEqual(match_repo(idx, '', 'utils.py — app').name, 'app')

    def test_vscode_process_name_differs_from_title_suffix(self):
        self.assertEqual(parse_ide_title('Code', 'main.py — worklog — Visual Studio Code'), ('worklog', 'main.py'))

    def test_title_with_separator_cannot_hide_a_private_window(self):
        tab = parse_tab_output('a|||b|||https://x.com|||incognito')
        self.assertTrue(tab.private)
        self.assertEqual((tab.title, tab.url), ('a|||b', 'https://x.com'))

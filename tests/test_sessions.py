import unittest
from datetime import datetime, timedelta

from summarizer.sessions import build_day, make_assigner

RULES = {'work': ['github.com'], 'personal': ['youtube.com']}
D = '2026-09-24'


def at(hhmm, sec=0):
    return f'{D}T{hhmm}:{sec:02d}'


def snap(hhmm, app='PyCharm', typ='coding', **extra):
    return {'ts': at(hhmm), 'app': app, 'window': '', 'type': typ, **extra}


def marker(hhmm, kind):
    return {'ts': at(hhmm), 'marker': kind}


def commit(hhmm, repo='api', message='feat: x', **extra):
    return {'ts': at(hhmm), 'source': 'git', 'repo': repo, 'type': 'coding', 'message': message, **extra}


def day(entries, commesse=None, **kw):
    return build_day(entries, commesse=commesse or [], rules=RULES, unknown_policy='hide',
                     heartbeat=300, **kw)


class Durations(unittest.TestCase):
    def test_each_sample_lasts_until_the_next(self):
        d = day([snap('09:00'), snap('09:20'), snap('09:40', 'Xcode'), marker('09:50', 'stop')])
        # a sample never lasts past 1.5 × the 300 s heartbeat (7.5 min), even if the next one is 20 min away;
        # the last one lasts a single heartbeat (5 min)
        b = d.blocks[0]
        self.assertAlmostEqual(b.minutes, 7.5 + 7.5 + 5, places=1)

    def test_last_sample_lasts_one_heartbeat(self):
        d = day([snap('09:00')])
        self.assertAlmostEqual(d.blocks[0].minutes, 5, places=1)

    def test_idle_marker_trims_and_drops(self):
        entries = [snap('09:00'), snap('09:05'), marker('09:07', 'idle_start'),
                   snap('09:09'), marker('09:30', 'idle_end'), snap('09:30')]
        d = day(entries)
        total = sum(s.minutes for b in d.blocks for s in b.samples)
        # 09:00→09:05 (5) + 09:05→09:07 (2, trimmed) + 09:30→09:35 (5); the 09:09 sample is dropped
        self.assertAlmostEqual(total, 12, places=1)

    def test_manual_range_wins_over_overlapping_samples(self):
        entries = [snap('09:00'), snap('09:05'),
                   {'ts': at('09:02'), 'end_ts': at('09:04'), 'type': 'meeting', 'manual': True, 'note': 'Call'}]
        d = day(entries)
        auto = sum(s.minutes for b in d.blocks for s in b.samples if not s.entry.get('manual'))
        manual = sum(s.minutes for b in d.blocks for s in b.samples if s.entry.get('manual'))
        self.assertAlmostEqual(manual, 2, places=1)
        self.assertAlmostEqual(auto, 5 + 5 - 2, places=1)

    def test_session_gap_splits_blocks(self):
        d = day([snap('09:00'), snap('09:05'), snap('11:00'), snap('11:05')])
        self.assertEqual(len(d.blocks), 2)

    def test_garbage_entries_are_ignored(self):
        d = day([{'ts': 'nope', 'app': 'x'}, {'app': 'no ts'}, snap('09:00')])
        self.assertEqual(len(d.blocks), 1)


class Privacy(unittest.TestCase):
    def browser(self, hhmm, **kw):
        return {'ts': at(hhmm), 'app': 'Google Chrome', 'window': '', 'type': 'browser', **kw}

    def test_private_personal_unclassified_dropped_and_counted(self):
        entries = [
            self.browser('09:00', excluded=True, reason='private'),
            self.browser('09:05', domain='youtube.com', site_class='personal'),
            self.browser('09:10', domain='competitor.com', site_class='unknown'),
            self.browser('09:15', domain='github.com', site_class='work', tab_title='PR', url='https://github.com/x'),
            marker('09:20', 'stop'),
        ]
        d = day(entries)
        self.assertEqual(len(d.blocks), 1)
        self.assertAlmostEqual(d.blocks[0].minutes, 5, places=1)
        self.assertAlmostEqual(d.excluded_minutes['private'], 5, places=1)
        self.assertAlmostEqual(d.excluded_minutes['personal-domain'], 5, places=1)
        self.assertAlmostEqual(d.excluded_minutes['unclassified-domain'], 5, places=1)
        self.assertEqual(list(d.unclassified_domains), ['competitor.com'])

    def test_old_entries_without_domain_are_judged_by_url_today(self):
        entries = [self.browser('09:00', url='https://www.youtube.com/watch?v=1', tab_title='Video'),
                   self.browser('09:05', url='https://github.com/a?b=1', tab_title='Repo')]
        d = day(entries + [marker('09:10', 'stop')])
        self.assertEqual(len(d.blocks), 1)
        self.assertEqual(d.blocks[0].samples[0].entry['tab_title'], 'Repo')

    def test_reclassifying_a_domain_applies_retroactively(self):
        entries = [self.browser('09:00', domain='intranet.corp', site_class='unknown'), marker('09:05', 'stop')]
        self.assertEqual(day(entries).blocks, [])
        rules = {'work': ['intranet.corp'], 'personal': []}
        d = build_day(entries, commesse=[], rules=rules, unknown_policy='hide', heartbeat=300)
        self.assertEqual(len(d.blocks), 1)

    def test_entry_marked_personal_is_dropped(self):
        d = day([snap('09:00', excluded=True), snap('09:05'), marker('09:10', 'stop')])
        self.assertAlmostEqual(d.blocks[0].minutes, 5, places=1)
        self.assertAlmostEqual(d.excluded_minutes['marked-personal'], 5, places=1)

    def test_excluded_entry_ends_previous_sample(self):
        # an ignored/private stretch must not be credited to the app before it
        d = day([snap('09:00'), {'ts': at('09:02'), 'app': 'worklog', 'type': 'other',
                                 'excluded': True, 'reason': 'ignored-app'}, marker('09:30', 'stop')])
        self.assertEqual(len(d.blocks), 0)   # only 2 min before the ignored stretch → dropped as short
        self.assertAlmostEqual(d.excluded_minutes['ignored-app'], 5, places=1)


class Assignment(unittest.TestCase):
    COMMESSE = [
        {'name': 'Screening', 'client': 'DDH', 'keywords': ['KH-682']},
        {'name': 'Tech4All', 'client': 'DDH', 'keywords': ['libertas-tech4all']},
        {'name': 'Solo', 'client': 'Acme', 'keywords': []},
    ]

    def test_keywords_are_whole_tokens(self):
        a = make_assigner(self.COMMESSE)
        self.assertEqual(a(['feature/KH-682-fix']), 'Screening')
        self.assertIsNone(a(['feature/KH-6820-other']))
        self.assertEqual(a(['libertas-tech4all']), 'Tech4All')

    def test_client_counts_only_when_unique(self):
        a = make_assigner(self.COMMESSE)
        self.assertIsNone(a([], ['ddh']))            # shared by two commesse → ambiguous
        self.assertEqual(a(['acme portal']), 'Solo')  # unique client

    def test_samples_and_commits_are_grouped_by_commessa(self):
        entries = [
            snap('09:00', project='libertas-tech4all'), snap('09:05', project='libertas-tech4all'),
            snap('09:10', app='Slack', typ='communication'), marker('09:15', 'stop'),
        ]
        d = day(entries, self.COMMESSE)
        assigned = {b.assignment: b.minutes for b in d.blocks}
        self.assertAlmostEqual(assigned['Tech4All'], 10, places=1)
        self.assertAlmostEqual(assigned[None], 5, places=1)

    def test_unassigned_work_inherits_from_the_repos_next_commit(self):
        entries = [snap(f'09:{m:02d}', project='api') for m in range(0, 40, 5)] + [
            commit('09:40', repo='api', message='fix', branch='feature/KH-682-x'),
            marker('09:45', 'stop'),
        ]
        d = day(entries, self.COMMESSE)
        self.assertEqual({b.assignment for b in d.blocks}, {'Screening'})
        self.assertEqual(len(d.blocks), 1)
        self.assertEqual(len(d.blocks[0].commits), 1)

    def test_no_inheritance_across_repos_or_after_two_hours(self):
        entries = [snap('09:00', project='web'),
                   commit('09:04', repo='api', branch='KH-682'),
                   snap('12:00', project='api'), marker('12:05', 'stop')]
        d = day(entries, self.COMMESSE)
        self.assertTrue(all(b.assignment in (None, 'Screening') for b in d.blocks))
        self.assertIn(None, {b.assignment for b in d.blocks if b.samples})

    def test_tag_mode_uses_first_tag(self):
        entries = [snap('09:00', tags=['ddh', 'backend']), snap('09:05'), marker('09:10', 'stop')]
        d = day(entries)
        self.assertEqual({b.assignment for b in d.blocks}, {'#ddh', None})


class Commits(unittest.TestCase):
    def test_commit_attaches_to_the_session_that_contains_it(self):
        entries = [snap('09:00'), snap('09:05'), commit('09:03'), marker('09:10', 'stop')]
        d = day(entries)
        self.assertEqual(len(d.blocks), 1)
        self.assertEqual(len(d.blocks[0].commits), 1)

    def test_commit_with_old_git_timestamp_format_lands_in_the_right_place(self):
        # written by the old enricher: "YYYY-MM-DD HH:MM:SS +0200"
        naive = datetime(2026, 9, 24, 9, 3, 0)
        offset = naive.astimezone().strftime('%z')
        old = {'ts': f'{D} 09:03:00 {offset}', 'source': 'git', 'repo': 'api', 'message': 'x', 'type': 'coding'}
        d = day([snap('09:00'), snap('09:05'), old, marker('09:10', 'stop')])
        self.assertEqual(len(d.blocks), 1)
        self.assertEqual(len(d.blocks[0].commits), 1)

    def test_orphan_commit_gets_nominal_time(self):
        d = day([commit('14:00'), commit('14:10', message='y')])
        self.assertEqual(len(d.blocks), 1)
        b = d.blocks[0]
        self.assertEqual(len(b.commits), 2)
        self.assertEqual(b.hours, 0.25)

    def test_short_block_kept_only_with_a_commit(self):
        d = day([snap('09:00'), marker('09:03', 'stop')])
        self.assertEqual(d.blocks, [])
        self.assertAlmostEqual(d.excluded_minutes['short'], 3, places=1)
        d = day([snap('09:00'), commit('09:01'), marker('09:03', 'stop')])
        self.assertEqual(len(d.blocks), 1)


class Rounding(unittest.TestCase):
    def hours(self, minutes):
        start = datetime(2026, 9, 24, 9, 0)
        # one heartbeat sample every 5 min for `minutes` minutes
        entries = [{'ts': (start + timedelta(minutes=m)).isoformat(), 'app': 'x', 'type': 'coding'}
                   for m in range(0, minutes, 5)]
        return day(entries).blocks[0].hours

    def test_quarter_hour_grain(self):
        self.assertEqual(self.hours(5), 0.25)
        self.assertEqual(self.hours(35), 0.5)
        self.assertEqual(self.hours(50), 0.75)
        self.assertEqual(self.hours(60), 1.0)

    def test_total_is_the_sum_of_lines(self):
        d = day([snap('09:00'), marker('09:35', 'stop'), snap('14:00'), marker('14:50', 'stop')])
        self.assertEqual(d.total_hours, sum(b.hours for b in d.blocks))


if __name__ == '__main__':
    unittest.main()


class HeartbeatFromLog(unittest.TestCase):
    def test_longer_logged_heartbeat_widens_the_cap(self):
        # poller ran with a 600 s heartbeat; the summarizer's own config says 300
        entries = [{'ts': at('09:00'), 'marker': 'start', 'heartbeat': 600}, snap('09:00'), snap('09:10'),
                   marker('09:20', 'stop')]
        d = build_day(entries, commesse=[], rules=RULES, unknown_policy='hide', heartbeat=300)
        self.assertAlmostEqual(sum(s.minutes for b in d.blocks for s in b.samples), 20, places=1)

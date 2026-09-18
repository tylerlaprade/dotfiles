import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / 'scripts' / 'sync'))
import threeway  # noqa: E402


class MergeTest(unittest.TestCase):
    def test_no_base_keeps_the_live_machine(self):
        self.assertEqual(threeway.merge(None, {'a': 1}, {'a': 2, 'b': 3}), {'a': 1})

    def test_unchanged_sides_stay(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 1}, {'a': 1}), {'a': 1})

    def test_repo_change_is_applied(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 1}, {'a': 2}), {'a': 2})

    def test_live_change_is_kept(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 2}, {'a': 1}), {'a': 2})

    def test_live_wins_when_both_moved(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 2}, {'a': 3}), {'a': 2})

    def test_same_change_on_both_sides_needs_no_base(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 5}, {'a': 5}), {'a': 5})

    def test_key_added_in_repo_arrives(self):
        self.assertEqual(threeway.merge({}, {}, {'a': 1}), {'a': 1})

    def test_key_added_live_is_exported(self):
        self.assertEqual(threeway.merge({}, {'a': 1}, {}), {'a': 1})

    def test_key_removed_live_is_removed(self):
        self.assertEqual(threeway.merge({'a': 1}, {}, {'a': 1}), {})

    def test_key_removed_in_repo_is_removed_when_allowed(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 1}, {}), {})

    def test_key_removed_in_repo_is_kept_when_deletion_is_off(self):
        self.assertEqual(threeway.merge({'a': 1}, {'a': 1}, {}, repo_can_delete=False), {'a': 1})

    def test_nested_values_compare_by_content(self):
        base = {'a': {'type': 'int', 'value': 1}}
        local = {'a': {'type': 'int', 'value': 1}}
        repo = {'a': {'type': 'int', 'value': 2}}
        self.assertEqual(threeway.merge(base, local, repo), repo)

    def test_changes_lists_updates_and_removals(self):
        updates, removals = threeway.changes({'a': 1, 'b': 2}, {'a': 9, 'c': 3})
        self.assertEqual(updates, {'a': 9, 'c': 3})
        self.assertEqual(removals, ['b'])


class BaseStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_base_dir = threeway.BASE_DIR
        self.original_log_path = threeway.LOG_PATH
        threeway.BASE_DIR = os.path.join(self.directory.name, 'bases')
        threeway.LOG_PATH = os.path.join(self.directory.name, 'logs', 'sync.log')
        self.addCleanup(setattr, threeway, 'BASE_DIR', self.original_base_dir)
        self.addCleanup(setattr, threeway, 'LOG_PATH', self.original_log_path)

    def test_missing_base_is_none(self):
        self.assertIsNone(threeway.load_base('nothing'))

    def test_base_round_trips_through_nested_names(self):
        threeway.save_base('macos-defaults/com.apple.dock', {'a': 1})
        self.assertEqual(threeway.load_base('macos-defaults/com.apple.dock'), {'a': 1})

    def test_corrupt_base_is_none(self):
        os.makedirs(threeway.BASE_DIR)
        Path(threeway.base_path('broken')).write_text('{')
        self.assertIsNone(threeway.load_base('broken'))

    def test_applied_changes_are_logged(self):
        threeway.log_applied('macos-defaults', 'com.apple.dock', {'autohide': {'type': 'bool', 'value': True}}, ['tilesize'])
        lines = Path(threeway.LOG_PATH).read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('macos-defaults com.apple.dock autohide = ', lines[0])
        self.assertTrue(lines[1].endswith('com.apple.dock tilesize removed'))
        json.loads(lines[0].split(' = ', 1)[1])

    def test_nothing_applied_writes_no_log(self):
        threeway.log_applied('macos-defaults', 'com.apple.dock', {})
        self.assertFalse(os.path.exists(threeway.LOG_PATH))


if __name__ == '__main__':
    unittest.main()

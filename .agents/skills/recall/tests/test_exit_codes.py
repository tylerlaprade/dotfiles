"""Exit codes: what the process says when there is nothing to show.

The callers are agents, not people. A broken index that exited 0 with no
results would make every one of them conclude nothing about the query exists,
so "no results" and "cannot read the index" are different exits.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from support import Corpus, claude_entry, pointed_at, recall


class ExitCodes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")

    def run_main(self, *argv):
        """Run main() the way a shell would: stdout, stderr, and the exit code."""
        saved = sys.argv
        sys.argv = ["recall.py", *argv]
        out = io.StringIO()
        err = io.StringIO()
        try:
            with pointed_at(self.corpus, self.db), redirect_stdout(out), \
                    redirect_stderr(err):
                try:
                    recall.main()
                except SystemExit as e:
                    return out.getvalue(), err.getvalue(), e.code
                return out.getvalue(), err.getvalue(), 0
        finally:
            sys.argv = saved

    def test_a_query_that_matches_exits_zero(self):
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111",
                                   [claude_entry("a distinctive turn")])
        out, _, code = self.run_main("distinctive")
        self.assertEqual(code, 0)
        self.assertIn("Found 1 sessions", out)

    def test_a_query_that_matches_nothing_exits_one(self):
        self.corpus.claude_session("22222222-2222-2222-2222-222222222222",
                                   [claude_entry("a distinctive turn")])
        out, _, code = self.run_main("nothingmatchesxyz")
        self.assertEqual(code, 1)
        self.assertIn("No matching sessions found.", out)

    def test_an_empty_index_exits_one(self):
        out, _, code = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("No sessions in the time window.", out)

    def test_a_corrupt_index_exits_three(self):
        Path(self.db).write_bytes(b"this is not a sqlite database")
        _, err, code = self.run_main("anything")
        self.assertEqual(code, 3)
        self.assertIn("Cannot use the index", err)


class DegradedIndex(unittest.TestCase):
    """Exit 4: files were skipped while indexing, so the index is partial.

    A 4 replaces the 0 or the 1 a whole index would have given. The one wrong
    conclusion — "nothing exists" — is exactly what neither may produce.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.corpus = Corpus(self.tmp / "corpus")
        self.db = str(self.tmp / "index.db")

    def run_main(self, *argv):
        """Run main() the way a shell would: stdout, stderr, and the exit code."""
        saved = sys.argv
        sys.argv = ["recall.py", *argv]
        out = io.StringIO()
        err = io.StringIO()
        try:
            with pointed_at(self.corpus, self.db), redirect_stdout(out), \
                    redirect_stderr(err):
                try:
                    recall.main()
                except SystemExit as e:
                    return out.getvalue(), err.getvalue(), e.code
                return out.getvalue(), err.getvalue(), 0
        finally:
            sys.argv = saved

    def break_symlink(self, name):
        """A session file that cannot even be stat'd: glob finds the name,
        getmtime fails, and the run records the skip."""
        path = self.corpus.claude / "proj" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/nonexistent-recall-target")
        return str(path)

    def test_results_from_a_partial_index_exit_four(self):
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111",
                                   [claude_entry("a distinctive turn")])
        broken = self.break_symlink("broken.jsonl")
        out, err, code = self.run_main("distinctive")
        self.assertEqual(code, 4)
        self.assertIn("Found 1 sessions", out)
        self.assertIn("Skipped 1 session file during indexing:", err)
        self.assertIn(broken, err)

    def test_no_match_from_a_partial_index_exits_four_not_one(self):
        """The missing file might have held the match. Exiting 1 would let
        the caller conclude nothing exists, which a partial index cannot say."""
        self.corpus.claude_session("22222222-2222-2222-2222-222222222222",
                                   [claude_entry("a distinctive turn")])
        self.break_symlink("broken.jsonl")
        out, _, code = self.run_main("nothingmatchesxyz")
        self.assertEqual(code, 4)
        self.assertIn("No matching sessions found.", out)

    def test_a_listing_from_a_partial_index_exits_four_not_one(self):
        self.break_symlink("broken.jsonl")
        out, _, code = self.run_main()
        self.assertEqual(code, 4)
        self.assertIn("No sessions in the time window.", out)

    def test_skipped_files_are_named_up_to_ten_then_counted(self):
        self.corpus.claude_session("33333333-3333-3333-3333-333333333333",
                                   [claude_entry("a distinctive turn")])
        for n in range(12):
            self.break_symlink(f"broken-{n:02d}.jsonl")
        _, err, code = self.run_main("distinctive")
        self.assertEqual(code, 4)
        self.assertIn("Skipped 12 session files during indexing:", err)
        named = [line for line in err.splitlines()
                 if line.startswith("  ") and ".jsonl:" in line]
        self.assertEqual(len(named), 10)
        self.assertIn("  ... and 2 more", err)

    def test_an_unreadable_file_keeps_its_rows_and_the_run_reports_degraded(self):
        """The never-prune rule still holds — the session stays searchable —
        and the run still says the index is partial."""
        path = self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                          [claude_entry("indexed before the error")])
        self.run_main("indexed")
        os.remove(path)
        path.mkdir()
        self.corpus.stamp(path)
        out, err, code = self.run_main("indexed")
        self.assertEqual(code, 4)
        self.assertIn("Found 1 sessions", out)
        self.assertIn(str(path), err)


if __name__ == "__main__":
    unittest.main()

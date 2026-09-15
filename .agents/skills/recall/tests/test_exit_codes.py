"""Exit codes: what the process says when there is nothing to show.

The callers are agents, not people. A broken index that exited 0 with no
results would make every one of them conclude nothing about the query exists,
so "no results" and "cannot read the index" are different exits.
"""
from __future__ import annotations

import io
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


if __name__ == "__main__":
    unittest.main()

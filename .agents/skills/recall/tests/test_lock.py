"""Tests for the index lock.

Several sessions can run /recall at the same time. Without serializing the
indexing phase they all read the same resume points, all parse the same new
bytes, and all insert them — the same message lands in the index more than
once. The lock stops that, and must never turn a slow run into a hung one.
"""
from __future__ import annotations

import fcntl
import io
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from typing import TYPE_CHECKING

import recall
from support import Corpus, claude_entry, index, pointed_at

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def lock_held_elsewhere(lock_path: str) -> Generator[None, None, None]:
    """Hold the lock the way another session would.

    flock belongs to the open file description rather than the process, so a
    second handle on the same file contends with the first even from here.
    """
    with Path(lock_path).open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


class LockBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.tmp: Path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corpus: Corpus = Corpus(self.tmp / "corpus")
        self.db: str = str(self.tmp / "index.db")
        self.lock: str = self.db + ".lock"

        original = recall.LOCK_WAIT_SECONDS
        recall.LOCK_WAIT_SECONDS = 0.4
        self.addCleanup(setattr, recall, "LOCK_WAIT_SECONDS", original)

    def run_main(self, *argv: str) -> str:
        saved = sys.argv
        sys.argv = ["recall.py", *argv]
        buffer = io.StringIO()
        try:
            # The messages this prints are the behaviour under test, not output
            # the suite needs to show.
            # The exit codes are the contract; these tests read stdout and the
            # database, not the code.
            with pointed_at(self.corpus, self.db), redirect_stdout(buffer), \
                    redirect_stderr(io.StringIO()), suppress(SystemExit):
                recall.main()
        finally:
            sys.argv = saved
        return buffer.getvalue()

    def session_count(self) -> int:
        conn = sqlite3.connect(self.db)
        try:
            return recall.sql_count(conn, "SELECT COUNT(*) FROM sessions")
        finally:
            conn.close()

    def test_a_run_takes_the_lock_and_reports_holding_it(self) -> None:
        with pointed_at(self.corpus, self.db), recall.index_lock() as have_lock:
            self.assertTrue(have_lock)

    def test_a_waiter_gives_up_rather_than_hanging(self) -> None:
        self.corpus.claude_session("11111111-1111-1111-1111-111111111111",
                                   [claude_entry("a turn")])
        with lock_held_elsewhere(self.lock):
            started = time.monotonic()
            with pointed_at(self.corpus, self.db), recall.index_lock() as have_lock:
                self.assertFalse(have_lock)
            waited = time.monotonic() - started
        self.assertGreaterEqual(waited, recall.LOCK_WAIT_SECONDS)
        self.assertLess(waited, recall.LOCK_WAIT_SECONDS + 5)

    def test_a_waiter_that_gave_up_still_searches(self) -> None:
        """Degrading to a slightly stale index beats printing nothing."""
        self.corpus.claude_session("22222222-2222-2222-2222-222222222222",
                                   [claude_entry("findable text")])
        self.run_main("findable")
        with lock_held_elsewhere(self.lock):
            output = self.run_main("findable")
        self.assertIn("Found 1 sessions", output)

    def test_a_waiter_that_gave_up_does_not_index(self) -> None:
        """It also must not create or migrate the schema, which are writes the
        lock holder may have the database busy for."""
        self.corpus.claude_session("33333333-3333-3333-3333-333333333333",
                                   [claude_entry("a turn")])
        with lock_held_elsewhere(self.lock):
            self.run_main("turn")
        conn = sqlite3.connect(self.db)
        try:
            tables = {row[0] for row in recall.fetch_all(
                conn, "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        finally:
            conn.close()
        self.assertNotIn("sessions", tables)

    def test_reindex_does_not_rebuild_without_the_lock(self) -> None:
        """--reindex wipes both tables before refilling them. Doing that while
        another process is mid-index would leave the index in pieces."""
        self.corpus.claude_session("44444444-4444-4444-4444-444444444444",
                                   [claude_entry("a turn")])
        self.run_main("turn")
        before = self.session_count()
        with lock_held_elsewhere(self.lock):
            self.run_main("turn", "--reindex")
        self.assertEqual(self.session_count(), before)

    def test_a_waiter_reindexes_nothing_once_the_holder_has_finished(self) -> None:
        """The waiter re-reads the sessions table after it gets the lock, so a
        file the holder already indexed shows an unchanged mtime and is
        skipped. No signalling between the two is needed."""
        self.corpus.claude_session("55555555-5555-5555-5555-555555555555",
                                   [claude_entry("a turn")])
        self.run_main("turn")
        with pointed_at(self.corpus, self.db):
            conn = sqlite3.connect(self.db)
            try:
                recall.create_schema(conn)
                self.assertEqual(recall.index_sessions(conn).indexed, 0)
            finally:
                conn.close()


class ConcurrentIndexing(unittest.TestCase):
    """The reason the lock exists. Two runs indexing the same growing session
    must not each insert the same new messages."""

    def setUp(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.tmp: Path = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corpus: Corpus = Corpus(self.tmp / "corpus")
        self.db: str = str(self.tmp / "index.db")

    def marker_count(self, marker: str) -> int:
        conn = sqlite3.connect(self.db)
        try:
            row = recall.fetch_one(
                conn, "SELECT COUNT(*) FROM messages WHERE text = ?", (marker,))
            return recall.sql_int(row[0]) if row else 0
        finally:
            conn.close()

    def test_two_locked_runs_do_not_double_index_the_same_append(self) -> None:
        path = self.corpus.claude_session("55555555-5555-5555-5555-555555555555",
                                          [claude_entry("opening turn")])
        index(self.corpus, self.db)
        self.corpus.write(path, [claude_entry("appended once")])

        ready = threading.Barrier(2)

        def run() -> None:
            ready.wait()
            with recall.index_lock() as have_lock:
                if not have_lock:
                    return
                conn = sqlite3.connect(self.db)
                conn.execute("PRAGMA journal_mode=WAL")
                try:
                    recall.create_schema(conn)
                    recall.migrate_schema(conn)
                    recall.index_sessions(conn)
                finally:
                    conn.close()

        # pointed_at swaps module globals, so it belongs out here. Entering it
        # per thread would let one thread restore the real session directories
        # while the other was still scanning them.
        with pointed_at(self.corpus, self.db):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            for thread in threads:
                self.assertFalse(thread.is_alive(), "a thread did not finish")

        self.assertEqual(self.marker_count("appended once"), 1)


if __name__ == "__main__":
    unittest.main()

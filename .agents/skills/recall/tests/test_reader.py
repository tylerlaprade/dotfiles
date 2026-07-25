"""Tests for the incremental reader: read_complete_lines, tail_hash_at, resume_offset.

These three decide how much of a session file gets read and whether it is safe
to skip the part already indexed. Everything else in incremental indexing rests
on the offsets they produce.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from support import recall

DEFAULT = object()  # "leave this argument alone", since None is a real value here


def read_all(path, start=0):
    """Drain the reader, returning (lines, offset past the last complete one)."""
    lines, offset = [], start
    for line, line_end in recall.read_complete_lines(path, start):
        lines.append(line)
        offset = line_end
    return lines, offset


class ReadCompleteLines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, text, name="session.jsonl"):
        path = self.tmp / name
        path.write_bytes(text.encode("utf-8"))
        return str(path)

    def test_offset_lands_just_past_each_newline(self):
        path = self.write("aa\nbbb\nc\n")
        seen = list(recall.read_complete_lines(path))
        self.assertEqual([line for line, _ in seen], ["aa\n", "bbb\n", "c\n"])
        self.assertEqual([offset for _, offset in seen], [3, 7, 9])
        self.assertEqual(seen[-1][1], os.path.getsize(path))

    def test_partial_final_line_is_left_alone(self):
        """A transcript being written can stop mid-line. That fragment must not
        be parsed, and the offset must not move past it."""
        path = self.write('{"a":1}\n{"b":2}\n{"c":unfin')
        lines, offset = read_all(path)
        self.assertEqual(lines, ['{"a":1}\n', '{"b":2}\n'])
        self.assertEqual(offset, 16)
        self.assertLess(offset, os.path.getsize(path))

    def test_completing_that_line_yields_it_once(self):
        path = self.write('{"a":1}\n{"c":unfin')
        _, offset = read_all(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('ished"}\n')
        lines, new_offset = read_all(path, offset)
        self.assertEqual(lines, ['{"c":unfinished"}\n'])
        self.assertEqual(new_offset, os.path.getsize(path))

    def test_resumes_from_a_non_zero_start(self):
        path = self.write("one\ntwo\nthree\n")
        lines, offset = read_all(path, 4)
        self.assertEqual(lines, ["two\n", "three\n"])
        self.assertEqual(offset, 14)

    def test_line_spanning_a_chunk_boundary(self):
        """A single line longer than one read is common — assistant turns run to
        megabytes — and must come back whole."""
        long_line = "x" * (recall.CHUNK_BYTES * 2 + 17)
        path = self.write(f"first\n{long_line}\nlast\n")
        lines, offset = read_all(path)
        self.assertEqual(lines[1], long_line + "\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(offset, os.path.getsize(path))

    def test_multibyte_character_split_across_a_chunk_boundary(self):
        """Chunks are cut at byte counts, so a 3-byte character can straddle
        one. Splitting it would corrupt the text on the way into the index."""
        filler = "a" * (recall.CHUNK_BYTES - 1)
        path = self.write(f"{filler}中文\n")
        lines, _ = read_all(path)
        self.assertEqual(lines, [filler + "中文\n"])
        self.assertNotIn("�", lines[0])

    def test_empty_file_yields_nothing(self):
        path = self.write("")
        self.assertEqual(read_all(path), ([], 0))

    def test_file_with_no_newline_at_all_yields_nothing(self):
        path = self.write('{"only":"fragment"}')
        self.assertEqual(read_all(path), ([], 0))

    def test_start_at_end_of_file_yields_nothing(self):
        path = self.write("one\ntwo\n")
        self.assertEqual(read_all(path, 8), ([], 8))


class TailHash(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.path = str(self.tmp / "session.jsonl")
        Path(self.path).write_bytes(b"line one\nline two\nline three\n")
        self.size = os.path.getsize(self.path)

    def test_hash_is_stable_for_unchanged_bytes(self):
        self.assertEqual(recall.tail_hash_at(self.path, self.size),
                         recall.tail_hash_at(self.path, self.size))

    def test_appending_does_not_disturb_an_earlier_offset(self):
        before = recall.tail_hash_at(self.path, self.size)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("line four\n")
        self.assertEqual(recall.tail_hash_at(self.path, self.size), before)

    def test_hash_covers_at_most_the_window(self):
        big = self.tmp / "big.jsonl"
        big.write_bytes(b"z" * (recall.TAIL_WINDOW * 3) + b"\n")
        offset = os.path.getsize(big)
        self.assertEqual(recall.tail_hash_at(str(big), offset),
                         recall.tail_hash_at(str(big), offset))

    def test_offset_zero_has_no_hash(self):
        self.assertIsNone(recall.tail_hash_at(self.path, 0))

    def test_offset_past_the_end_has_no_hash(self):
        self.assertIsNone(recall.tail_hash_at(self.path, self.size + 500))

    def test_missing_file_has_no_hash(self):
        self.assertIsNone(recall.tail_hash_at(str(self.tmp / "gone.jsonl"), 10))


class ResumeOffset(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.path = str(self.tmp / "session.jsonl")
        Path(self.path).write_bytes(b"alpha\nbravo\ncharlie\n")
        self.offset = os.path.getsize(self.path)
        self.hash = recall.tail_hash_at(self.path, self.offset)

    def resume(self, offset=DEFAULT, tail_hash=DEFAULT, version=DEFAULT):
        return recall.resume_offset(
            self.path,
            self.offset if offset is DEFAULT else offset,
            self.hash if tail_hash is DEFAULT else tail_hash,
            recall.PARSER_VERSION if version is DEFAULT else version,
        )

    def test_resumes_when_the_file_was_only_appended_to(self):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("delta\n")
        self.assertEqual(self.resume(), self.offset)

    def test_refuses_when_a_message_was_removed_from_the_middle(self):
        """Claude drops a message by truncating and rewriting the tail, so
        bytes after the removal point shift and the stored offset is a lie."""
        lines = Path(self.path).read_bytes().split(b"\n")
        del lines[1]
        Path(self.path).write_bytes(b"\n".join(lines))
        self.assertEqual(self.resume(), 0)

    def test_refuses_when_removal_is_hidden_by_later_appends(self):
        """Size alone cannot catch this: the file ends up longer than the
        stored offset again, but the bytes underneath it are different."""
        lines = Path(self.path).read_bytes().split(b"\n")
        del lines[1]
        Path(self.path).write_bytes(b"\n".join(lines))
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("echo\nfoxtrot\ngolf\n")
        self.assertGreater(os.path.getsize(self.path), self.offset)
        self.assertEqual(self.resume(), 0)

    def test_refuses_when_the_file_was_truncated(self):
        Path(self.path).write_bytes(b"alpha\n")
        self.assertEqual(self.resume(), 0)

    def test_refuses_when_the_file_was_replaced_wholesale(self):
        """A temp-file-and-rename, the way Grok saves chat history."""
        replacement = self.tmp / "new.jsonl"
        replacement.write_bytes(b"whisky\nxray\nyankee\n")
        os.replace(replacement, self.path)
        self.assertEqual(self.resume(), 0)

    def test_refuses_without_a_stored_offset(self):
        self.assertEqual(self.resume(offset=0), 0)

    def test_refuses_without_a_stored_hash(self):
        self.assertEqual(self.resume(tail_hash=None), 0)

    def test_an_edit_inside_the_window_is_caught(self):
        big = self.tmp / "big.jsonl"
        big.write_bytes(b"z" * (recall.TAIL_WINDOW * 2) + b"\n")
        offset = os.path.getsize(big)
        stored = recall.tail_hash_at(str(big), offset)
        data = bytearray(big.read_bytes())
        data[offset - recall.TAIL_WINDOW] = ord("q")
        big.write_bytes(bytes(data))
        self.assertEqual(
            recall.resume_offset(str(big), offset, stored, recall.PARSER_VERSION), 0)

    def test_an_edit_just_outside_the_window_is_not(self):
        """The window is a deliberate boundary, not an oversight. Only the
        bytes it covers are checked, and nothing in these transcripts rewrites
        history without disturbing them — a removed message shifts everything
        after it, and a replaced file changes all of it."""
        big = self.tmp / "big.jsonl"
        big.write_bytes(b"z" * (recall.TAIL_WINDOW * 2) + b"\n")
        offset = os.path.getsize(big)
        stored = recall.tail_hash_at(str(big), offset)
        data = bytearray(big.read_bytes())
        data[offset - recall.TAIL_WINDOW - 1] = ord("q")
        big.write_bytes(bytes(data))
        self.assertEqual(
            recall.resume_offset(str(big), offset, stored, recall.PARSER_VERSION), offset)

    def test_refuses_when_the_parser_has_changed(self):
        """A parser that now keeps or drops different text must not be trusted
        with a prefix parsed by the old one."""
        self.assertEqual(self.resume(version=recall.PARSER_VERSION - 1), 0)


if __name__ == "__main__":
    unittest.main()

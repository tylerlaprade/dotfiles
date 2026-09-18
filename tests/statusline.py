import json
from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
from zoneinfo import ZoneInfo


REPOSITORY = Path(__file__).resolve().parents[1]
EASTERN = ZoneInfo('America/New_York')
WEDNESDAY = datetime(2026, 1, 14, tzinfo=EASTERN)
ESC = '\x1b'
RESET = '\x1b[0m'
# The narrowest terminal these lines land in is Claude Code's background pty
# host at 130 columns; anything wider wraps and leaves stale rows behind.
WIDTH_BUDGET = 120
CSI_FINAL = range(0x40, 0x7F)
OSC_HYPERLINK = ']8;'
PLAIN_GIT = '\x1b[37mFondly\x1b[0m \x1b[38;5;242mmaster\x1b[0m\x1b[36m \x1b[0m'
DIRTY_GIT = '\x1b[37mFondly\x1b[0m \x1b[38;5;242mmaster\x1b[0m\x1b[38;5;218m*\x1b[0m\x1b[36m \x1b[0m'
PR_GIT = ('\x1b[37mFondly\x1b[0m \x1b]8;;https://app.graphite.dev/github/pr/example/project/12\x1b\\'
          '\x1b[32m#12\x1b[0m \x1b[37mAdd the launch banner\x1b[0m\x1b]8;;\x1b\\\x1b[36m \x1b[0m')
USAGE_OK = {'ok': True, 'five_hour': 6, 'seven_day': 3, 'fable': 4}
USAGE_SHIM = '#!/bin/bash\nprintf "%s\\n" "${FAKE_USAGE:-}"\nexit "${FAKE_USAGE_STATUS:-0}"\n'


def at(hour, minute, second=0, day=WEDNESDAY):
    return day.replace(hour=hour, minute=minute, second=second)


def visible(text):
    return re.sub(r'\x1b\[[0-9;]*m|\x1b\]8;;[^\x07\x1b]*(?:\x07|\x1b\\)', '', text)


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)


def scan_line(line):
    """Walk one line byte by byte and return every escape-grammar problem."""
    problems = []
    index = 0
    open_attributes = False
    while index < len(line):
        char = line[index]
        if char != ESC:
            if ord(char) < 0x20 or char == '\x7f':
                problems.append(f'control byte {char!r} at {index}')
            index += 1
            continue
        kind = line[index + 1:index + 2]
        if kind == '[':
            end = index + 2
            while end < len(line) and ord(line[end]) not in CSI_FINAL:
                if line[end] == ESC or ord(line[end]) < 0x20:
                    break
                end += 1
            if end >= len(line) or ord(line[end]) not in CSI_FINAL:
                problems.append(f'unterminated CSI at {index}: {line[index:end + 1]!r}')
                index = end
                continue
            body = line[index + 2:end]
            if not re.fullmatch(r'[0-9;?]*', body):
                problems.append(f'unexpected CSI body {body!r} at {index}')
            if line[end] == 'm':
                parameters = body.split(';')
                for position, parameter in enumerate(parameters):
                    if parameter == '38' and parameters[position + 1:position + 2] == ['2']:
                        channels = parameters[position + 2:position + 5]
                        if len(channels) != 3 or not all(channel.isdigit() and int(channel) <= 255 for channel in channels):
                            problems.append(f'malformed truecolor SGR {body!r} at {index}')
                open_attributes = parameters not in (['0'], [''])
            index = end + 1
        elif kind == ']':
            if not line.startswith(ESC + OSC_HYPERLINK, index):
                problems.append(f'OSC other than a hyperlink at {index}: {line[index:index + 12]!r}')
            end = index + 2
            while end < len(line) and line[end] != '\x07' and line[end] != ESC:
                end += 1
            if line.startswith(ESC + '\\', end):
                index = end + 2
            elif line.startswith('\x07', end):
                index = end + 1
            else:
                problems.append(f'unterminated OSC at {index}')
                index = end
        else:
            problems.append(f'bare ESC before {kind!r} at {index}')
            index += 1
    if open_attributes:
        problems.append('line ends with SGR attributes still open')
    return problems


class StatuslineTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.calls = self.root / 'calls'
        source = (REPOSITORY / '.claude/statusline.sh').read_text()
        self.statusline = self.root / 'statusline.sh'
        self.statusline.write_text(source.replace('/tmp/claude-rate-limits.json', str(self.root / 'rates.json')))
        self.install('date', '#!/bin/bash\n'
                     'for argument in "$@"; do [ "$argument" = -r ] && exec /bin/date "$@"; done\n'
                     'exec /bin/date -r "$FAKE_NOW" "$@"\n')
        self.install('pkill', '#!/bin/bash\n'
                     f'printf "pkill %s\\n" "$*" >> {str(self.calls)!r}\n'
                     'exit 1\n')
        self.install('git-status-line', '#!/bin/bash\n'
                     'printf "%b" "${FAKE_GIT:-}"\n')
        self.install('claude-usage', USAGE_SHIM)
        jq = shutil.which('jq')
        self.assertIsNotNone(jq, 'jq is required')
        self.environment = {
            'HOME': str(self.home),
            'PATH': f'{self.bin}:{Path(jq).parent}:/usr/bin:/bin',
            'FAKE_GIT': PLAIN_GIT,
        }

    def install(self, name, source):
        target = self.bin / name
        target.write_text(source)
        target.chmod(0o755)

    def payload(self, tokens=50000, window=200000, rates=None, model='Opus 5', effort='max', cost=None):
        payload = {'workspace': {'current_dir': str(self.workspace)},
                   'context_window': {'total_input_tokens': tokens, 'context_window_size': window}}
        if model is not None:
            payload['model'] = {'display_name': model}
        if effort is not None:
            payload['effort'] = {'level': effort}
        if rates is not None:
            payload['rate_limits'] = rates
        if cost is not None:
            payload['cost'] = {'total_cost_usd': cost}
        return payload

    def rates(self, now, five_hour=6, seven_day=3, five_hour_reset=None, seven_day_reset=None):
        five_hour_reset = five_hour_reset or now + timedelta(hours=2)
        seven_day_reset = seven_day_reset or now + timedelta(days=6, hours=15)
        return {'five_hour': {'used_percentage': five_hour, 'resets_at': int(five_hour_reset.timestamp())},
                'seven_day': {'used_percentage': seven_day, 'resets_at': int(seven_day_reset.timestamp())}}

    def render(self, payload, now=at(15, 0), usage=USAGE_OK, usage_status=0, git=PLAIN_GIT, usage_command=True):
        environment = dict(self.environment, FAKE_NOW=str(int(now.timestamp())), FAKE_GIT=git,
                           FAKE_USAGE_STATUS=str(usage_status))
        if usage is not None:
            environment['FAKE_USAGE'] = json.dumps(usage)
        shim = self.bin / 'claude-usage'
        if usage_command:
            self.install('claude-usage', USAGE_SHIM)
        else:
            shim.unlink(missing_ok=True)
        result = subprocess.run(['bash', str(self.statusline)], input=json.dumps(payload).encode(),
                                env=environment, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'', 'the script wrote to stderr')
        self.assertFalse(self.calls.exists(), 'pkill was invoked')
        output = result.stdout.decode('utf-8')
        self.assertTrue(output.endswith('\n'), repr(output))
        lines = output[:-1].split('\n')
        self.assertTrue(1 <= len(lines) <= 3, lines)
        for line in lines:
            self.assertEqual(scan_line(line), [], repr(line))
            self.assertLessEqual(display_width(visible(line)), WIDTH_BUDGET, repr(visible(line)))
        return lines

    def assert_context_line(self, line, expected_percent, expected_tokens, expected_clock, prefix='Opus 5 max · '):
        text = visible(line)
        match = re.fullmatch(r'(.*)([▓▒░]{10}) (\d+)% · (\S+) · (\d{1,2}:\d\d [AP]M)', text)
        self.assertIsNotNone(match, text)
        self.assertEqual(match.group(1), prefix)
        self.assertEqual(int(match.group(3)), expected_percent)
        self.assertEqual(match.group(4), expected_tokens)
        self.assertEqual(match.group(5), expected_clock)
        filled = min(expected_percent // 10, 10)
        self.assertEqual(match.group(2)[:filled], '▓' * filled, text)
        if filled < 10:
            self.assertEqual(match.group(2)[filled], '▒' if expected_percent * 10 % 100 >= 50 else '░', text)
            self.assertEqual(match.group(2)[filled + 1:], '░' * (9 - filled), text)

    def test_context_pressure_across_both_windows(self):
        for window, unit in ((200000, '200k'), (1000000, '1m')):
            for percent in (0, 10, 55, 60, 75, 90, 95, 96, 100, 120, 200):
                tokens = window * percent // 100
                with self.subTest(window=window, percent=percent):
                    lines = self.render(self.payload(tokens=tokens, window=window))
                    used = f'{tokens // 1000}k' if tokens < 1000000 else f'{tokens / 1000000:g}m'
                    self.assert_context_line(lines[0], percent, f'{used}/{unit}', '3:00 PM')

    def test_model_label_variants(self):
        for model, effort, prefix in (('Opus 5', 'max', 'Opus 5 max · '), ('Opus 5', None, 'Opus 5 · '),
                                      ('Sonnet 5 (1M context)', 'high', 'Sonnet 5 high · '), (None, None, '')):
            with self.subTest(model=model, effort=effort):
                lines = self.render(self.payload(model=model, effort=effort))
                self.assert_context_line(lines[0], 25, '50k/200k', '3:00 PM', prefix=prefix)

    def test_clock_color_follows_the_time_of_day(self):
        cases = [
            (at(15, 0), '3:00 PM', {'\x1b[97m3:00 PM'}, {'\x1b[1m', '\x1b[7m'}),
            (at(16, 45), '4:45 PM', {'\x1b[38;2;0;200;0m'}, {'\x1b[1m', '\x1b[7m', '\x1b[97m'}),
            (at(16, 50), '4:50 PM', {'\x1b[38;2;'}, {'\x1b[1m', '\x1b[7m', '\x1b[97m'}),
            (at(17, 10), '5:10 PM', {'\x1b[1m'}, {'\x1b[7m'}),
            (at(18, 40, 10), '6:40 PM', {'\x1b[1m', '\x1b[7m'}, set()),
            (at(18, 40, 40), '6:40 PM', {'\x1b[1m'}, {'\x1b[7m'}),
            (at(22, 0), '10:00 PM', {'\x1b[38;2;'}, {'\x1b[1m', '\x1b[7m'}),
            (at(23, 21), '11:21 PM', {'\x1b[1m'}, {'\x1b[7m'}),
            (at(0, 30, day=WEDNESDAY + timedelta(days=1)), '12:30 AM', {'\x1b[1m', '\x1b[7m'}, set()),
            (at(0, 30, 45, day=WEDNESDAY + timedelta(days=1)), '12:30 AM', {'\x1b[1m'}, {'\x1b[7m'}),
        ]
        for now, clock, present, absent in cases:
            with self.subTest(now=now.isoformat()):
                lines = self.render(self.payload(), now=now)
                self.assert_context_line(lines[0], 25, '50k/200k', clock)
                clock_part = lines[0][lines[0].rindex(' · ') + 3:]
                for needle in present:
                    self.assertIn(needle, clock_part)
                for needle in absent:
                    self.assertNotIn(needle, clock_part)

    def test_rate_limits_with_every_reset_distance(self):
        now = at(15, 0)
        base_usage = dict(USAGE_OK, resets_5h=int((now + timedelta(hours=2)).timestamp()),
                          resets_7d=int((now + timedelta(days=6, hours=15)).timestamp()))
        cases = {
            'today': (self.rates(now), 'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 4%'),
            'tomorrow': (self.rates(now, five_hour_reset=now + timedelta(hours=20)),
                         'Usage · 5h 6% (resets in 20h 0m at 11:00 AM tomorrow) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 4%'),
            'next week': (self.rates(now, seven_day_reset=now + timedelta(days=5, hours=3)),
                          'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 5d 3h at Mon 6:00 PM) · Fable 4%'),
            'expired': (self.rates(now, five_hour_reset=now - timedelta(minutes=5)),
                        'Usage · 5h 6% · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 4%'),
            'same weekly reset as fable': (self.rates(now), None),
        }
        for name, (rates, expected) in cases.items():
            usage = dict(base_usage, resets_fable=base_usage['resets_7d'] + 1) if name == 'same weekly reset as fable' else base_usage
            with self.subTest(case=name):
                lines = self.render(self.payload(rates=rates), now=now, usage=usage)
                self.assertEqual(len(lines), 3)
                if expected is not None:
                    self.assertEqual(visible(lines[1]), expected)
                else:
                    self.assertEqual(visible(lines[1]), cases['today'][1])

    def test_exhausted_five_hour_window_shows_cost(self):
        now = at(15, 0)
        lines = self.render(self.payload(rates=self.rates(now, five_hour=100, seven_day=100), cost=12.5), now=now)
        text = visible(lines[1])
        self.assertTrue(text.startswith('Usage · 5h $12.50 (resets in 2h 0m at 5:00 PM) · 7d 100%+ (resets in'), text)
        lines = self.render(self.payload(rates=self.rates(now, five_hour=100, seven_day=100)), now=now)
        self.assertTrue(visible(lines[1]).startswith('Usage · 5h 100%+ (resets in'), visible(lines[1]))

    def test_usage_fetch_outcomes(self):
        now = at(15, 0)
        cases = {
            'ok': (USAGE_OK, 0, True, 'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 4%'),
            'keychain unavailable': ({'ok': False, 'error': 'keychain unavailable'}, 1, True,
                                     'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable: keychain unavailable'),
            'login required': ({'ok': False, 'error': 'HTTP 401', 'fable': 40}, 1, True,
                               'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable: login required'),
            'rate limited': ({'ok': False, 'error': 'HTTP 429', 'fable': 73}, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 73% · rate limited'),
            'fetch failed': ({'ok': False, 'error': 'boom', 'fable': 10}, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable 10% · fetch failed'),
            'empty result': (None, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM) · Fable unavailable'),
            'command missing': (None, 0, False,
                                'Usage · 5h 6% (resets in 2h 0m at 5:00 PM) · 7d 3% (resets in 6d 15h at Wed 6:00 AM)'),
        }
        for name, (usage, status, command, expected) in cases.items():
            with self.subTest(case=name):
                lines = self.render(self.payload(rates=self.rates(now)), now=now, usage=usage, usage_status=status,
                                    usage_command=command)
                self.assertEqual(visible(lines[1]), expected)

    def test_stdin_rates_absent_falls_back_to_the_usage_fetch(self):
        now = at(15, 0)
        usage = dict(USAGE_OK, five_hour=20, seven_day=30, resets_5h=int((now + timedelta(hours=1)).timestamp()),
                     resets_7d=int((now + timedelta(days=2)).timestamp()))
        lines = self.render(self.payload(), now=now, usage=usage)
        self.assertEqual(visible(lines[1]), 'Usage · 5h 20% (resets in 1h 0m at 4:00 PM) · 7d 30% (resets in 2d 0h at Fri 3:00 PM) · Fable 4%')
        lines = self.render(self.payload(), now=now, usage=None, usage_status=1, usage_command=False)
        self.assertEqual(len(lines), 2)
        self.assertEqual(visible(lines[1]), 'Fondly master ')

    def test_git_line_variants(self):
        for name, git, expected in (('plain', PLAIN_GIT, 'Fondly master '), ('dirty', DIRTY_GIT, 'Fondly master* '),
                                    ('pull request', PR_GIT, 'Fondly #12 Add the launch banner ')):
            with self.subTest(case=name):
                lines = self.render(self.payload(), git=git)
                self.assertEqual(len(lines), 3)
                self.assertEqual(visible(lines[2]), expected)
                self.assertEqual(lines[2], git)
        lines = self.render(self.payload(), git='')
        self.assertEqual(len(lines), 2)


if __name__ == '__main__':
    unittest.main()

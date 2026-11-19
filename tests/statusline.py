from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import final, override
from zoneinfo import ZoneInfo

type JSONValue = bool | int | float | str | list[JSONValue] | dict[str, JSONValue] | None

REPOSITORY = Path(__file__).resolve().parents[1]
EASTERN = ZoneInfo('America/New_York')
WEDNESDAY = datetime(2026, 1, 14, tzinfo=EASTERN)
ESC = '\x1b'
DELETE = '\x7f'
FIRST_PRINTABLE = 0x20
RESET = '\x1b[0m'
# The narrowest terminal these lines land in is Claude Code's background pty
# host at 130 columns; anything wider wraps and leaves stale rows behind.
WIDTH_BUDGET = 120
MAX_LINES = 3
CSI_FINAL = range(0x40, 0x7F)
OSC_HYPERLINK = ']8;'
TRUECOLOR_CHANNELS = 3
MAX_CHANNEL = 255
BAR_CELLS = 10
PERCENT_PER_CELL = 10
HALF_CELL_PERCENT = 5
COMPACTION_PERCENT = 100
THOUSAND = 1000
MILLION = 1_000_000
PLAIN_GIT = '\x1b[37mFondly\x1b[0m \x1b[38;5;242mmaster\x1b[0m\x1b[36m \x1b[0m'
DIRTY_GIT = '\x1b[37mFondly\x1b[0m \x1b[38;5;242mmaster\x1b[0m\x1b[38;5;218m*\x1b[0m\x1b[36m \x1b[0m'
PR_GIT = ('\x1b[37mFondly\x1b[0m \x1b]8;;https://app.graphite.dev/github/pr/example/project/12\x1b\\'
          '\x1b[32m#12\x1b[0m \x1b[37mAdd the launch banner\x1b[0m\x1b]8;;\x1b\\\x1b[36m \x1b[0m')
USAGE_OK: dict[str, JSONValue] = {'ok': True, 'five_hour': 6, 'seven_day': 3, 'fable': 4}
USAGE_SHIM = '#!/bin/bash\nprintf "%s\\n" "${FAKE_USAGE:-}"\nexit "${FAKE_USAGE_STATUS:-0}"\n'
DEFAULTS_SHIM = ('#!/bin/bash\n'
                 'printf "%s\\n" "$*" >> "$DEFAULTS_CALLS"\n'
                 'case "$*" in\n'
                 '  "read -g AppleInterfaceStyle") [ "${FAKE_STYLE:-}" = Dark ] && echo Dark ;;\n'
                 '  "read com.superlogical.rex Workspace.appearance") [ -n "${FAKE_REX_APPEARANCE:-}" ] && echo "$FAKE_REX_APPEARANCE" ;;\n'
                 '  "read com.superlogical.rex Workspace.lightThemeID") echo paper ;;\n'
                 '  "read com.superlogical.rex Workspace.darkThemeID") echo night ;;\n'
                 '  *) exit 1 ;;\n'
                 'esac\n')
LSAPPINFO_SHIM = ('#!/bin/bash\n'
                  '[ "$*" = "info -only bundlepath com.superlogical.rex" ] && [ -n "${FAKE_REX_APP:-}" ] '
                  '&& printf \'"LSBundlePath"="%s"\\n\' "$FAKE_REX_APP"\n')
type RGB = tuple[int, int, int]
TOMORROW_BLUE: RGB = (129, 162, 190)
WHITE: RGB = (255, 255, 255)
RED: RGB = (255, 0, 0)
REX_THEMES = 'Contents/Resources/SuperUI_SuperDesignTokens.bundle/Contents/Resources/themes.json'


@dataclass(frozen=True)
class Theme:
    background: str
    foreground: str
    blue: str
    green: str
    yellow: str
    red: str

    def rex_entry(self, theme_id: str) -> dict[str, JSONValue]:
        ansi: list[JSONValue] = ['#000000', self.red, self.green, self.yellow, self.blue]
        return {'id': theme_id, 'source': {'background': self.background, 'foreground': self.foreground, 'ansi': ansi}}

    def ghostty_file(self) -> str:
        return (f'background = {self.background}\nforeground = {self.foreground}\n'
                f'palette = 1={self.red}\npalette = 2={self.green}\npalette = 3={self.yellow}\npalette = 4={self.blue}\n')


PAPER = Theme('#ffffff', '#1f2328', '#0969da', '#116329', '#f0c674', '#cf222e')
NIGHT = Theme('#0d1117', '#e6edf3', '#58a6ff', '#3fb950', '#d29922', '#ff7b72')


def rgb(hex_color: str) -> RGB:
    value = int(hex_color.lstrip('#'), 16)
    return (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def color_before(line: str, text: str) -> RGB:
    match = re.search(rf'\x1b\[(?:1;)?38;2;(\d+);(\d+);(\d+)m{re.escape(text)}', line)
    if match is None:
        raise AssertionError(f'no truecolor before {text!r} in {line!r}')
    red, green, blue = (int(value) for value in match.groups())
    return (red, green, blue)


def at(hour: int, minute: int, second: int = 0, day: datetime = WEDNESDAY) -> datetime:
    return day.replace(hour=hour, minute=minute, second=second)


AFTERNOON = at(15, 0)


def visible(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m|\x1b\]8;;[^\x07\x1b]*(?:\x07|\x1b\\)', '', text)


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)


def token_count(tokens: int) -> str:
    return f'{tokens // THOUSAND}k' if tokens < MILLION else f'{tokens // (MILLION // 10) / 10:g}m'


def file_reference(source: str, file_name: str) -> str:
    match = re.search(rf'/\S*/{re.escape(file_name)}', source)
    if match is None:
        raise ValueError(f'the script no longer mentions {file_name}')
    return match.group(0)


def is_control(char: str) -> bool:
    return ord(char) < FIRST_PRINTABLE


def truecolor_problems(parameters: list[str], body: str, index: int) -> list[str]:
    problems: list[str] = []
    for position, parameter in enumerate(parameters):
        if parameter == '38' and parameters[position + 1:position + 2] == ['2']:
            channels = parameters[position + 2:position + 5]
            if len(channels) != TRUECOLOR_CHANNELS or not all(channel.isdigit() and int(channel) <= MAX_CHANNEL for channel in channels):
                problems.append(f'malformed truecolor SGR {body!r} at {index}')
    return problems


def scan_csi(line: str, index: int, problems: list[str]) -> tuple[int, list[str] | None]:
    end = index + 2
    while end < len(line) and ord(line[end]) not in CSI_FINAL:
        if line[end] == ESC or is_control(line[end]):
            break
        end += 1
    if end >= len(line) or ord(line[end]) not in CSI_FINAL:
        problems.append(f'unterminated CSI at {index}: {line[index:end + 1]!r}')
        return end, None
    body = line[index + 2:end]
    if not re.fullmatch(r'[0-9;?]*', body):
        problems.append(f'unexpected CSI body {body!r} at {index}')
    if line[end] != 'm':
        return end + 1, None
    parameters = body.split(';')
    problems.extend(truecolor_problems(parameters, body, index))
    return end + 1, parameters


def scan_osc(line: str, index: int, problems: list[str]) -> int:
    if not line.startswith(ESC + OSC_HYPERLINK, index):
        problems.append(f'OSC other than a hyperlink at {index}: {line[index:index + 12]!r}')
    end = index + 2
    while end < len(line) and line[end] != '\x07' and line[end] != ESC:
        end += 1
    if line.startswith(ESC + '\\', end):
        return end + 2
    if line.startswith('\x07', end):
        return end + 1
    problems.append(f'unterminated OSC at {index}')
    return end


def scan_line(line: str) -> list[str]:
    """Walk one line byte by byte and return every escape-grammar problem."""
    problems: list[str] = []
    index = 0
    open_attributes = False
    while index < len(line):
        char = line[index]
        if char != ESC:
            if is_control(char) or char == DELETE:
                problems.append(f'control byte {char!r} at {index}')
            index += 1
            continue
        kind = line[index + 1:index + 2]
        if kind == '[':
            index, parameters = scan_csi(line, index, problems)
            if parameters is not None:
                open_attributes = parameters not in (['0'], [''])
        elif kind == ']':
            index = scan_osc(line, index, problems)
        else:
            problems.append(f'bare ESC before {kind!r} at {index}')
            index += 1
    if open_attributes:
        problems.append('line ends with SGR attributes still open')
    return problems


@dataclass(frozen=True)
class Session:
    tokens: int = 50000
    window: int = 200000
    rates: dict[str, JSONValue] | None = None
    model: str | None = 'Opus 5'
    effort: str | None = 'max'
    cost: float | None = None


@dataclass(frozen=True)
class Fakes:
    now: datetime = AFTERNOON
    usage: JSONValue = field(default_factory=USAGE_OK.copy)
    usage_status: int = 0
    git: str = PLAIN_GIT
    usage_command: bool = True
    claude_environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextLine:
    percent: int
    tokens: str
    clock: str
    prefix: str = 'Opus 5 max · '
    due: bool = False


DEFAULT_SESSION = Session()
DEFAULT_FAKES = Fakes()


def weekly_colors(line: str) -> list[tuple[int, int, int]]:
    match = re.search(r'7d \x1b\[38;2;(\d+);(\d+);(\d+)m.*?\x1b\[38;2;(\d+);(\d+);(\d+)m\d+%.*?resets in \x1b\[38;2;(\d+);(\d+);(\d+)m', line)
    if match is None:
        raise AssertionError(f'no weekly battery in {line!r}')
    values = [int(value) for value in match.groups()]
    return [(values[0], values[1], values[2]), (values[3], values[4], values[5]), (values[6], values[7], values[8])]


@final
class StatuslineTest(unittest.TestCase):
    @override
    def setUp(self) -> None:
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
        self.statusline.write_text(source.replace(file_reference(source, 'claude-rate-limits.json'), str(self.root / 'rates.json')))
        self.statusline.chmod(0o755)
        self.age(self.statusline)
        self.install('date', '#!/bin/bash\n'
                     'for argument in "$@"; do [ "$argument" = -r ] && exec /bin/date "$@"; done\n'
                     'exec /bin/date -r "$FAKE_NOW" "$@"\n')
        self.install('pkill', '#!/bin/bash\n'
                     f'printf "pkill %s\\n" "$*" >> {str(self.calls)!r}\n'
                     'exit 1\n')
        self.install('git-status-line', '#!/bin/bash\n'
                     'printf "%b" "${FAKE_GIT:-}"\n')
        self.install('claude-usage', USAGE_SHIM)
        self.install('defaults', DEFAULTS_SHIM)
        self.install('lsappinfo', LSAPPINFO_SHIM)
        self.defaults_calls = self.root / 'defaults-calls'
        self.defaults_calls.touch()
        jq = shutil.which('jq')
        if jq is None:
            self.fail('jq is required')
        self.environment = {
            'HOME': str(self.home),
            'PATH': f'{self.bin}:{Path(jq).parent}:/usr/bin:/bin',
            'FAKE_GIT': PLAIN_GIT,
            'TZ': EASTERN.key,
            'TMPDIR': str(self.root),
            'DEFAULTS_CALLS': str(self.defaults_calls),
        }

    def install(self, name: str, source: str) -> None:
        target = self.bin / name
        target.write_text(source)
        target.chmod(0o755)

    def age(self, path: Path) -> None:
        past = time.time() - 3600
        os.utime(path, (past, past))

    def freshen(self, path: Path) -> None:
        future = time.time() + 3600
        os.utime(path, (future, future))

    def preference(self, name: str) -> Path:
        path = self.home / 'Library/Preferences' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.age(path)
        return path

    def install_rex(self) -> dict[str, str]:
        themes = self.root / 'Rex.app' / REX_THEMES
        themes.parent.mkdir(parents=True)
        themes.write_text(json.dumps([PAPER.rex_entry('paper'), NIGHT.rex_entry('night')]))
        self.age(themes)
        self.preference('com.superlogical.rex.plist')
        self.preference('.GlobalPreferences.plist')
        return {'TERM_PROGRAM': 'rex', 'FAKE_REX_APP': str(self.root / 'Rex.app')}

    def palette_line(self, environment: dict[str, str], seven_day: int = 10) -> str:
        now = at(15, 0)
        rates = self.rates(now, seven_day=seven_day, seven_day_reset=now + timedelta(days=6, hours=20))
        del rates['five_hour']
        session = Session(tokens=171000, rates=rates)
        lines = self.render(self.payload(session), Fakes(now=now, usage=dict(USAGE_OK, five_hour=None), claude_environment=environment))
        return lines[0] + lines[1]

    def assert_theme_colors(self, line: str, theme: Theme) -> None:
        self.assertEqual(color_before(line, '│'), rgb(theme.foreground), line)
        self.assertEqual(weekly_colors(line)[2], rgb(theme.blue), line)
        self.assertEqual(color_before(line, 'compaction due'), rgb(theme.yellow), line)

    def payload(self, session: Session = DEFAULT_SESSION) -> dict[str, JSONValue]:
        payload: dict[str, JSONValue] = {
            'workspace': {'current_dir': str(self.workspace)},
            'context_window': {'total_input_tokens': session.tokens, 'context_window_size': session.window},
        }
        if session.model is not None:
            payload['model'] = {'display_name': session.model}
        if session.effort is not None:
            payload['effort'] = {'level': session.effort}
        if session.rates is not None:
            payload['rate_limits'] = session.rates
        if session.cost is not None:
            payload['cost'] = {'total_cost_usd': session.cost}
        return payload

    def rates(self, now: datetime, five_hour: int = 6, seven_day: int = 3,
              five_hour_reset: datetime | None = None, seven_day_reset: datetime | None = None) -> dict[str, JSONValue]:
        five_hour_reset = five_hour_reset or now + timedelta(hours=2)
        seven_day_reset = seven_day_reset or now + timedelta(days=6, hours=15)
        return {'five_hour': {'used_percentage': five_hour, 'resets_at': int(five_hour_reset.timestamp())},
                'seven_day': {'used_percentage': seven_day, 'resets_at': int(seven_day_reset.timestamp())}}

    def render(self, payload: dict[str, JSONValue], fakes: Fakes = DEFAULT_FAKES) -> list[str]:
        environment = dict(self.environment, FAKE_NOW=str(int(fakes.now.timestamp())), FAKE_GIT=fakes.git,
                           FAKE_USAGE_STATUS=str(fakes.usage_status), **fakes.claude_environment)
        if fakes.usage is not None:
            environment['FAKE_USAGE'] = json.dumps(fakes.usage)
        shim = self.bin / 'claude-usage'
        if fakes.usage_command:
            self.install('claude-usage', USAGE_SHIM)
        else:
            shim.unlink(missing_ok=True)
        result = subprocess.run([str(self.statusline)], input=json.dumps(payload).encode(),
                                env=environment, capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'', 'the script wrote to stderr')
        self.assertFalse(self.calls.exists(), 'pkill was invoked')
        output = result.stdout.decode('utf-8')
        self.assertTrue(output.endswith('\n'), repr(output))
        lines = output[:-1].split('\n')
        self.assertTrue(1 <= len(lines) <= MAX_LINES, lines)
        for line in lines:
            self.assertEqual(scan_line(line), [], repr(line))
            self.assertLessEqual(display_width(visible(line)), WIDTH_BUDGET, repr(visible(line)))
        return lines

    def assert_context_line(self, line: str, expected: ContextLine) -> None:
        text = visible(line)
        match = re.fullmatch(r'(.*)([▓▒░]{10}) (\d+)% · (\S+)( · compaction due)? · (\d{1,2}:\d\d [AP]M)', text)
        if match is None:
            self.fail(text)
        self.assertEqual(match.group(1), expected.prefix)
        self.assertEqual(int(match.group(3)), expected.percent)
        self.assertEqual(match.group(4), expected.tokens)
        self.assertEqual(match.group(5) is not None, expected.due)
        self.assertEqual(match.group(6), expected.clock)
        filled = min(expected.percent // PERCENT_PER_CELL, BAR_CELLS)
        self.assertEqual(match.group(2)[:filled], '▓' * filled, text)
        if filled < BAR_CELLS:
            half_filled = expected.percent % PERCENT_PER_CELL >= HALF_CELL_PERCENT
            self.assertEqual(match.group(2)[filled], '▒' if half_filled else '░', text)
            self.assertEqual(match.group(2)[filled + 1:], '░' * (BAR_CELLS - 1 - filled), text)

    def test_context_pressure_across_both_windows(self) -> None:
        for window, limit, unit in ((200000, 167000, '167k'), (1000000, 967000, '967k')):
            for percent in (0, 10, 55, 60, 75, 90, 95, 96, 100, 120, 200):
                tokens = limit * percent // 100
                with self.subTest(window=window, percent=percent):
                    lines = self.render(self.payload(Session(tokens=tokens, window=window)))
                    expected = ContextLine(percent, f'{token_count(tokens)}/{unit}', '3:00 PM', due=percent >= COMPACTION_PERCENT)
                    self.assert_context_line(lines[0], expected)

    def test_context_limit_follows_auto_compact_settings(self) -> None:
        user_settings = self.home / '.claude/settings.json'
        user_settings.parent.mkdir()
        cases: tuple[tuple[str, int, dict[str, str], dict[str, JSONValue] | None, str], ...] = (
            ('Claudex compact window', 272000, {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '258400'}, None, '225k'),
            ('compact window floor', 1000000, {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '50000'}, None, '67k'),
            ('invalid compact window', 1000000, {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': 'junk'}, None, '967k'),
            ('/autocompact setting', 1000000, {}, {'autoCompactWindow': 500000}, '467k'),
            ('environment beats setting', 1000000, {'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '300000'},
             {'autoCompactWindow': 500000}, '267k'),
            ('smaller output reserve', 1000000, {'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '8000'}, None, '979k'),
            ('larger output limit', 1000000, {'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '64000'}, None, '967k'),
            ('percent override', 1000000, {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '50'}, None, '490k'),
            ('percent override above reserve', 1000000, {'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE': '100'}, None, '967k'),
            ('auto-compact disabled', 1000000, {'DISABLE_AUTO_COMPACT': '1'}, None, '1m'),
            ('compaction disabled', 1000000, {'DISABLE_COMPACT': 'true'}, None, '1m'),
            ('auto-compact setting off', 1000000, {}, {'autoCompactEnabled': False}, '1m'),
        )
        for name, window, claude_environment, settings, limit in cases:
            with self.subTest(name):
                user_settings.write_text(json.dumps(settings or {}))
                lines = self.render(self.payload(Session(tokens=10000, window=window)), Fakes(claude_environment=claude_environment))
                self.assertIn(f' · 10k/{limit} · ', visible(lines[0]))

    def test_compaction_due_follows_auto_compact(self) -> None:
        cases: tuple[tuple[str, dict[str, str], bool], ...] = (
            ('auto-compact past limit', {}, True),
            ('auto-compact disabled', {'DISABLE_AUTO_COMPACT': '1'}, False),
        )
        for name, claude_environment, due in cases:
            with self.subTest(name):
                lines = self.render(self.payload(Session(tokens=171000)), Fakes(claude_environment=claude_environment))
                self.assertEqual('compaction due' in visible(lines[0]), due)

    def test_project_settings_override_user_settings(self) -> None:
        for directory, window in ((self.home, 500000), (self.workspace, 400000)):
            (directory / '.claude').mkdir()
            (directory / '.claude/settings.json').write_text(json.dumps({'autoCompactWindow': window}))
        (self.workspace / '.claude/settings.local.json').write_text(json.dumps({'autoCompactWindow': 300000}))
        lines = self.render(self.payload(Session(tokens=10000, window=1000000)))
        self.assertIn(' · 10k/267k · ', visible(lines[0]))

    def test_model_label_variants(self) -> None:
        for model, effort, prefix in (('Opus 5', 'max', 'Opus 5 max · '), ('Opus 5', None, 'Opus 5 · '),
                                      ('Sonnet 5 (1M context)', 'high', 'Sonnet 5 high · '), (None, None, '')):
            with self.subTest(model=model, effort=effort):
                lines = self.render(self.payload(Session(model=model, effort=effort)))
                self.assert_context_line(lines[0], ContextLine(29, '50k/167k', '3:00 PM', prefix=prefix))

    def test_clock_color_follows_the_time_of_day(self) -> None:
        cases: list[tuple[datetime, str, set[str], set[str]]] = [
            (at(15, 0), '3:00 PM', {'\x1b[38;2;255;255;255m3:00 PM'}, {'\x1b[1m', '\x1b[7m'}),
            (at(16, 45), '4:45 PM', {'\x1b[38;2;181;189;104m'}, {'\x1b[1m', '\x1b[7m', '\x1b[38;2;255;255;255m'}),
            (at(16, 50), '4:50 PM', {'\x1b[38;2;'}, {'\x1b[1m', '\x1b[7m', '\x1b[38;2;255;255;255m'}),
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
                lines = self.render(self.payload(), Fakes(now=now))
                self.assert_context_line(lines[0], ContextLine(29, '50k/167k', clock))
                clock_part = lines[0][lines[0].rindex(' · ') + 3:]
                for needle in present:
                    self.assertIn(needle, clock_part)
                for needle in absent:
                    self.assertNotIn(needle, clock_part)

    def test_rate_limits_with_every_reset_distance(self) -> None:
        now = at(15, 0)
        resets_7d = int((now + timedelta(days=6, hours=15)).timestamp())
        base_usage = dict(USAGE_OK, resets_5h=int((now + timedelta(hours=2)).timestamp()), resets_7d=resets_7d)
        cases = {
            'today': (self.rates(now), 'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 4%'),
            'tomorrow': (self.rates(now, five_hour_reset=now + timedelta(hours=20)),
                         'Usage · 5h 6% (resets in 20h 0m) · 7d 3% (resets in 6d 15h) · Fable 4%'),
            'next week': (self.rates(now, seven_day_reset=now + timedelta(days=5, hours=3)),
                          'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 5d 3h) · Fable 4%'),
            'expired': (self.rates(now, five_hour_reset=now - timedelta(minutes=5)),
                        'Usage · 5h 6% · 7d 3% (resets in 6d 15h) · Fable 4%'),
            'same weekly reset as fable': (self.rates(now), None),
        }
        for name, (rates, expected) in cases.items():
            usage = dict(base_usage, resets_fable=resets_7d + 1) if name == 'same weekly reset as fable' else base_usage
            with self.subTest(case=name):
                lines = self.render(self.payload(Session(rates=rates)), Fakes(now=now, usage=usage))
                self.assertEqual(len(lines), MAX_LINES)
                if expected is not None:
                    self.assertEqual(visible(lines[1]), expected)
                else:
                    self.assertEqual(visible(lines[1]), cases['today'][1])

    def render_without_five_hour_limit(self, now: datetime, seven_day: int, left: timedelta) -> str:
        rates = self.rates(now, seven_day=seven_day, seven_day_reset=now + left)
        del rates['five_hour']
        lines = self.render(self.payload(Session(rates=rates)), Fakes(now=now, usage=dict(USAGE_OK, five_hour=None)))
        return lines[1]

    def test_weekly_battery_marks_the_even_burn_pace(self) -> None:
        now = at(15, 0)
        cases = {
            'behind pace': (15, timedelta(days=3, hours=12), '▓▒░░░│░░░░ 15%'),
            'on pace': (50, timedelta(days=3, hours=12), '▓▓▓▓▓│░░░░ 50%'),
            'ahead of pace': (62, timedelta(days=5), '▓▓│▓▓▓░░░░ 62%'),
            'nearly out': (96, timedelta(hours=18), '▓▓▓▓▓▓▓▓│▒ 96%'),
            'over the limit': (100, timedelta(days=1), '▓▓▓▓▓▓▓▓│▓ 100%+'),
        }
        for name, (seven_day, left, expected) in cases.items():
            with self.subTest(case=name):
                line = self.render_without_five_hour_limit(now, seven_day, left)
                self.assertIn(f'Usage · 7d {expected} (resets in', visible(line))
                battery, percent, _ = weekly_colors(line)
                self.assertEqual(battery, percent, line)

    def test_weekly_battery_and_percent_follow_pace(self) -> None:
        now = at(15, 0)
        ahead = weekly_colors(self.render_without_five_hour_limit(now, 45, timedelta(days=6, hours=6)))[0]
        behind = weekly_colors(self.render_without_five_hour_limit(now, 45, timedelta(days=1)))[0]
        self.assertGreater(ahead[0], 200)
        self.assertLess(ahead[1], 100)
        self.assertLess(behind[0], 200)

    def test_countdown_shows_how_soon_the_reset_is(self) -> None:
        now = at(15, 0)
        fresh = weekly_colors(self.render_without_five_hour_limit(now, 10, timedelta(days=6, hours=20)))[2]
        closing = weekly_colors(self.render_without_five_hour_limit(now, 10, timedelta(hours=2)))[2]
        self.assertEqual(fresh, TOMORROW_BLUE)
        self.assertGreater(closing[0], 200)
        self.assertLess(closing[1], 150)

    def test_five_hour_percent_follows_pace(self) -> None:
        now = at(15, 0)
        rates = self.rates(now, five_hour=50, five_hour_reset=now + timedelta(hours=4, minutes=30))
        line = self.render(self.payload(Session(rates=rates)), Fakes(now=now))[1]
        match = re.search(r'5h \x1b\[38;2;(\d+);(\d+);(\d+)m50%', line)
        if match is None:
            self.fail(line)
        red, green, _ = (int(value) for value in match.groups())
        self.assertGreater(red, 200)
        self.assertLess(green, 100)

    def test_weekly_battery_has_no_pace_mark_without_a_reset_time(self) -> None:
        text = visible(self.render_without_five_hour_limit(at(15, 0), 30, timedelta(minutes=-5)))
        self.assertIn('Usage · 7d ▓▓▓░░░░░░░ 30%', text)

    def render_weekly_limits(self, now: datetime, seven_day: int, fable: int, left: timedelta, fable_left: timedelta | None = None) -> str:
        rates = self.rates(now, seven_day=seven_day, seven_day_reset=now + left)
        del rates['five_hour']
        usage = dict(USAGE_OK, five_hour=None, seven_day=seven_day, fable=fable, resets_7d=int((now + left).timestamp()),
                     resets_fable=int((now + (fable_left or left)).timestamp()))
        return self.render(self.payload(Session(rates=rates)), Fakes(now=now, usage=usage))[1]

    def test_shared_weekly_reset_puts_one_countdown_next_to_usage(self) -> None:
        now = at(15, 0)
        line = self.render_weekly_limits(now, 35, 70, timedelta(days=3, hours=12))
        self.assertEqual(visible(line), 'Usage (resets in 3d 12h) · 7d ▓▓▓▒░│░░░░ 35% · Fable ▓▓▓▓▓│▓░░░ 70%')
        fable = re.search(r'Fable \x1b\[38;2;(\d+;\d+;\d+)m.*?\x1b\[38;2;(\d+;\d+;\d+)m70%', line)
        if fable is None:
            self.fail(line)
        self.assertEqual(fable.group(1), fable.group(2), line)

    def test_shared_countdown_color_follows_time_not_usage(self) -> None:
        now = at(15, 0)
        countdown = r'Usage\x1b\[0m \(resets in \x1b\[38;2;(\d+;\d+;\d+)m'
        heavy = re.search(countdown, self.render_weekly_limits(now, 35, 90, timedelta(days=3, hours=12)))
        light = re.search(countdown, self.render_weekly_limits(now, 5, 5, timedelta(days=3, hours=12)))
        if heavy is None or light is None:
            self.fail('no shared countdown')
        self.assertEqual(heavy.group(1), light.group(1))

    def test_different_weekly_resets_keep_their_own_countdowns(self) -> None:
        now = at(15, 0)
        line = self.render_weekly_limits(now, 55, 40, timedelta(days=2, hours=3), timedelta(days=5, hours=1))
        self.assertEqual(visible(line), 'Usage · 7d ▓▓▓▓▓▒│░░░ 55% (resets in 2d 3h) · Fable ▓▓│▓░░░░░░ 40% (resets in 5d 1h)')

    def test_clock_and_resets_follow_the_local_time_zone(self) -> None:
        now = at(15, 0)
        lines = self.render(self.payload(Session(rates=self.rates(now))), Fakes(now=now, claude_environment={'TZ': 'Asia/Kathmandu'}))
        self.assert_context_line(lines[0], ContextLine(29, '50k/167k', '1:45 AM'))
        self.assertEqual(visible(lines[1]), 'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 4%')

    def test_exhausted_five_hour_window_shows_cost(self) -> None:
        now = at(15, 0)
        lines = self.render(self.payload(Session(rates=self.rates(now, five_hour=100, seven_day=100), cost=12.5)), Fakes(now=now))
        text = visible(lines[1])
        self.assertTrue(text.startswith('Usage · 5h $12.50 (resets in 2h 0m) · 7d 100%+ (resets in'), text)
        lines = self.render(self.payload(Session(rates=self.rates(now, five_hour=100, seven_day=100))), Fakes(now=now))
        self.assertTrue(visible(lines[1]).startswith('Usage · 5h 100%+ (resets in'), visible(lines[1]))

    def test_usage_fetch_outcomes(self) -> None:
        now = at(15, 0)
        cases: dict[str, tuple[JSONValue, int, bool, str]] = {
            'ok': (USAGE_OK, 0, True, 'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 4%'),
            'keychain unavailable': ({'ok': False, 'error': 'keychain unavailable'}, 1, True,
                                     'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable unavailable'),
            'keychain unavailable with cache': ({'ok': False, 'error': 'keychain unavailable', 'fable': 40}, 1, True,
                                                'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 40% · stale'),
            'python missing': ({'ok': False, 'error': 'no python', 'fable': 40}, 1, True,
                               'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable: brew install python'),
            'login required': ({'ok': False, 'error': 'HTTP 401', 'fable': 40}, 1, True,
                               'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable: login required'),
            'rate limited': ({'ok': False, 'error': 'HTTP 429', 'fable': 73}, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 73% · rate limited'),
            'fetch failed': ({'ok': False, 'error': 'boom', 'fable': 10}, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable 10% · fetch failed'),
            'empty result': (None, 1, True,
                             'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h) · Fable unavailable'),
            'command missing': (None, 0, False,
                                'Usage · 5h 6% (resets in 2h 0m) · 7d 3% (resets in 6d 15h)'),
        }
        for name, (usage, status, command, expected) in cases.items():
            with self.subTest(case=name):
                lines = self.render(self.payload(Session(rates=self.rates(now))),
                                    Fakes(now=now, usage=usage, usage_status=status, usage_command=command))
                self.assertEqual(visible(lines[1]), expected)

    def test_stdin_rates_absent_falls_back_to_the_usage_fetch(self) -> None:
        now = at(15, 0)
        usage = dict(USAGE_OK, five_hour=20, seven_day=30, resets_5h=int((now + timedelta(hours=1)).timestamp()),
                     resets_7d=int((now + timedelta(days=2)).timestamp()))
        lines = self.render(self.payload(), Fakes(now=now, usage=usage))
        self.assertEqual(visible(lines[1]), 'Usage · 5h 20% (resets in 1h 0m) · 7d 30% (resets in 2d 0h) · Fable 4%')
        lines = self.render(self.payload(), Fakes(now=now, usage=None, usage_status=1, usage_command=False))
        self.assertEqual(len(lines), 2)
        self.assertEqual(visible(lines[1]), 'Fondly master ')

    def test_git_line_variants(self) -> None:
        for name, git, expected in (('plain', PLAIN_GIT, 'Fondly master '), ('dirty', DIRTY_GIT, 'Fondly master* '),
                                    ('pull request', PR_GIT, 'Fondly #12 Add the launch banner ')):
            with self.subTest(case=name):
                lines = self.render(self.payload(), Fakes(git=git))
                self.assertEqual(len(lines), MAX_LINES)
                self.assertEqual(visible(lines[2]), expected)
                self.assertEqual(lines[2], git)
        lines = self.render(self.payload(), Fakes(git=''))
        self.assertEqual(len(lines), MAX_LINES)
        self.assertEqual(visible(lines[2]), str(self.workspace))

    def test_rex_theme_follows_the_appearance(self) -> None:
        rex = self.install_rex()
        cases = (('system light', '', '', PAPER), ('system dark', '', 'Dark', NIGHT),
                 ('Rex light over system dark', 'light', 'Dark', PAPER), ('Rex dark over system light', 'dark', '', NIGHT))
        for name, rex_appearance, style, theme in cases:
            with self.subTest(name):
                cache = self.root / name
                cache.mkdir()
                line = self.palette_line(dict(rex, TMPDIR=str(cache), FAKE_REX_APPEARANCE=rex_appearance, FAKE_STYLE=style))
                self.assert_theme_colors(line, theme)

    def test_spent_limit_is_pure_red_in_every_theme(self) -> None:
        rex = self.install_rex()
        for style in ('', 'Dark'):
            with self.subTest(style=style):
                cache = self.root / f'alarm{style}'
                cache.mkdir()
                line = self.palette_line(dict(rex, TMPDIR=str(cache), FAKE_STYLE=style), seven_day=100)
                self.assertEqual(color_before(line, '100%+'), RED, line)

    def test_unreadable_rex_theme_falls_back_to_tomorrow_night(self) -> None:
        rex = self.install_rex()
        cases = {'unknown appearance': dict(rex, FAKE_REX_APPEARANCE='sepia'),
                 'Rex app not running': {'TERM_PROGRAM': 'rex'},
                 'other terminal': {'TERM_PROGRAM': 'Apple_Terminal'}}
        for name, environment in cases.items():
            with self.subTest(name):
                cache = self.root / name
                cache.mkdir()
                line = self.palette_line(dict(environment, TMPDIR=str(cache)))
                self.assertEqual(color_before(line, '│'), WHITE, line)
                self.assertEqual(weekly_colors(line)[2], TOMORROW_BLUE, line)

    def test_ghostty_theme_follows_its_config(self) -> None:
        config = self.home / '.config/ghostty'
        (config / 'themes').mkdir(parents=True)
        (config / 'config').write_text('font-size = 18\ntheme = "light:Paper,dark:Night"\nforeground = #445566\n')
        (config / 'themes/Paper').write_text(PAPER.ghostty_file())
        resources = self.root / 'ghostty-resources'
        (resources / 'themes').mkdir(parents=True)
        (resources / 'themes/Night').write_text(NIGHT.ghostty_file())
        self.preference('.GlobalPreferences.plist')
        for style, theme in (('', PAPER), ('Dark', NIGHT)):
            with self.subTest(theme=theme.background):
                cache = self.root / f'ghostty{style}'
                cache.mkdir()
                line = self.palette_line({'TERM_PROGRAM': 'ghostty', 'GHOSTTY_RESOURCES_DIR': str(resources),
                                          'TMPDIR': str(cache), 'FAKE_STYLE': style})
                self.assertEqual(color_before(line, '│'), (0x44, 0x55, 0x66), line)
                self.assertEqual(weekly_colors(line)[2], rgb(theme.blue), line)

    def test_saved_palette_is_reused_until_a_source_changes(self) -> None:
        rex = self.install_rex()
        environment = dict(rex, FAKE_STYLE='')
        self.assert_theme_colors(self.palette_line(environment), PAPER)
        self.assertNotEqual(self.defaults_calls.read_text(), '')
        self.defaults_calls.write_text('')
        self.assert_theme_colors(self.palette_line(dict(environment, FAKE_STYLE='Dark')), PAPER)
        self.assertEqual(self.defaults_calls.read_text(), '')
        self.freshen(self.home / 'Library/Preferences/.GlobalPreferences.plist')
        self.assert_theme_colors(self.palette_line(dict(environment, FAKE_STYLE='Dark')), NIGHT)
        self.assertNotEqual(self.defaults_calls.read_text(), '')

    def test_saved_palette_follows_a_moved_app_and_waits_for_a_running_one(self) -> None:
        rex = self.install_rex()
        not_running = {'TERM_PROGRAM': 'rex', 'FAKE_STYLE': ''}
        self.assertEqual(weekly_colors(self.palette_line(not_running))[2], TOMORROW_BLUE)
        self.assertFalse((self.root / 'statusline-palette-rex').exists())
        self.assert_theme_colors(self.palette_line(dict(rex, FAKE_STYLE='')), PAPER)
        moved = self.root / 'Rex Beta.app'
        (self.root / 'Rex.app').rename(moved)
        (moved / REX_THEMES).write_text(json.dumps([PAPER.rex_entry('night'), NIGHT.rex_entry('paper')]))
        self.age(moved / REX_THEMES)
        self.assert_theme_colors(self.palette_line(dict(rex, FAKE_REX_APP=str(moved), FAKE_STYLE='')), NIGHT)

    def test_folder_outside_git_abbreviates_home(self) -> None:
        folder = self.home / 'Code'
        folder.mkdir()
        payload = self.payload()
        payload['workspace'] = {'current_dir': str(folder)}
        lines = self.render(payload, Fakes(git=''))
        self.assertEqual(visible(lines[2]), '~/Code')


if __name__ == '__main__':
    unittest.main()

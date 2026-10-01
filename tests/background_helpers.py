from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Union, final

if TYPE_CHECKING:
    from collections.abc import Callable

JSONValue = Union[None, bool, int, float, str, list["JSONValue"], dict[str, "JSONValue"]]

parse_json: Callable[[str], JSONValue] = json.loads

REPOSITORY = Path(__file__).resolve().parents[1]
FAKE_CREDENTIAL = 'example-token'
EXPIRED_LOGIN = json.dumps({'claudeAiOauth': {'accessToken': FAKE_CREDENTIAL, 'expiresAt': 1600000000000}})


def json_object(value: JSONValue) -> dict[str, JSONValue]:
    if isinstance(value, dict):
        return value
    raise TypeError(f'expected a JSON object, got {value!r}')


def usage_report(text: str) -> dict[str, JSONValue]:
    return json_object(parse_json(text))


def shell_assignment(source: str, name: str) -> str:
    match = re.search(rf'^{name}=(\S+)$', source, re.MULTILINE)
    if match is None:
        raise ValueError(f'the script no longer assigns {name}=')
    return match.group(1)


def file_reference(source: str, file_name: str) -> str:
    match = re.search(rf'/\S*/{re.escape(file_name)}', source)
    if match is None:
        raise ValueError(f'the script no longer mentions {file_name}')
    return match.group(0)


@final
class BackgroundHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'scripts/bin'
        self.bin.mkdir(parents=True)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.environment = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:{os.environ["PATH"]}')
        for name in ['GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN']:
            self.environment.pop(name, None)
        (self.root / 'scripts/keychain-unlocked.py').write_text(
            'import os, sys\nsys.exit(int(os.environ.get("FAKE_KEYCHAIN_STATUS", "0")))\n')
        self.cache = self.root / 'claude-usage.json'
        usage_source = (REPOSITORY / 'scripts/bin/claude-usage.sh').read_text()
        live_cache = shell_assignment(usage_source, 'cache')
        live_lock = shell_assignment(usage_source, 'lock')
        for name in ['gh-background', 'gh-pr-lookup', 'gh-pr-status', 'git-status-line', 'claude-usage']:
            source = (REPOSITORY / f'scripts/bin/{name}.sh').read_text()
            source = source.replace(live_cache, str(self.cache)).replace(live_lock, str(self.root / 'usage.fetch'))
            self.install(name, source)
        self.calls = self.root / 'calls'
        self.install('osascript', '#!/bin/bash\n'
                     f'printf "osascript\\n" >> {str(self.calls)!r}\n')
        self.install('gh', '#!/bin/bash\n'
                     f'printf "%s\\n" "$*" >> {str(self.calls)!r}\n'
                     'sleep "${FAKE_DELAY:-0}"\n'
                     'printf "%s" "${FAKE_RESULT:-}"\n'
                     'printf "%s" "${FAKE_STDERR:-}" >&2\n'
                     'exit "${FAKE_STATUS:-4}"\n')
        self.install('security', '#!/bin/bash\n'
                     f'printf "security %s\\n" "$*" >> {str(self.calls)!r}\n'
                     'if [ -n "${FAKE_SECURITY_DELAY:-}" ]; then sleep "$FAKE_SECURITY_DELAY"; fi\n'
                     'printf "%s" "${FAKE_SECRET:-}"\n'
                     'exit "${FAKE_SECURITY_STATUS:-44}"\n')

    def install(self, name: str, source: str) -> None:
        target = self.bin / name
        target.write_text(source)
        target.chmod(0o755)

    def run_helper(self, name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.bin / name), *arguments], env=self.environment,
                              text=True, capture_output=True, timeout=5, check=False)

    def wait_for_map(self) -> str:
        deadline = time.monotonic() + 4
        while not (self.home / '.cache/gh-pr-map').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return (self.home / '.cache/gh-pr-map').read_text()

    def test_gh_runs_without_prompts_and_classifies_failures(self) -> None:
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 11)
        self.environment.update(FAKE_STATUS='1', FAKE_STDERR='gh: Bad credentials (HTTP 401)\n')
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 11)
        self.environment.update(FAKE_STATUS='1', FAKE_STDERR='gh: Not Found (HTTP 404)\n')
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 12)
        self.environment.update(FAKE_STATUS='0', FAKE_STDERR='', FAKE_RESULT='{"login":"Tyler"}')
        result = self.run_helper('gh-background', 'api', 'user')
        self.assertEqual((result.returncode, result.stdout), (0, '{"login":"Tyler"}'))

    def test_pr_failure_is_cached_and_visible(self) -> None:
        first = self.run_helper('gh-pr-lookup', 'example', 'feature/[test]')
        self.assertEqual(first.stdout, '!\tlogin required\n')
        second = self.run_helper('gh-pr-lookup', 'example', 'feature/[test]', '--async')
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_locked_keychain_skips_background_github(self) -> None:
        self.environment['FAKE_KEYCHAIN_STATUS'] = '1'
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 12)
        self.assertFalse(self.calls.exists())

    def test_github_environment_token_does_not_need_unlocked_keychain(self) -> None:
        self.environment.update(FAKE_KEYCHAIN_STATUS='1', GH_TOKEN=FAKE_CREDENTIAL,
                                FAKE_STATUS='0', FAKE_RESULT='{"login":"Tyler"}')
        result = self.run_helper('gh-background', 'api', 'user')
        self.assertEqual((result.returncode, result.stdout), (0, '{"login":"Tyler"}'))

    def test_missing_keychain_check_skips_credential_commands(self) -> None:
        (self.root / 'scripts/keychain-unlocked.py').unlink()
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 12)
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'keychain unavailable')
        self.assertFalse(self.calls.exists())

    def test_unresponsive_keychain_check_skips_credential_commands(self) -> None:
        (self.root / 'scripts/keychain-unlocked.py').write_text(
            'import time\ntime.sleep(30)\n')
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 12)
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'keychain unavailable')
        self.assertFalse(self.calls.exists())

    def test_locked_keychain_preserves_usage_without_reading_credentials(self) -> None:
        self.environment['FAKE_KEYCHAIN_STATUS'] = '1'
        self.cache.write_text(json.dumps({'ok': True, 'fable': 23, 'fetched_at': 0}))
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'keychain unavailable')
        self.assertEqual(usage_report(result.stdout)['fable'], 23)
        self.assertFalse(self.calls.exists())

    def test_usage_recovers_when_keychain_is_unlocked(self) -> None:
        self.environment['FAKE_KEYCHAIN_STATUS'] = '1'
        self.run_helper('claude-usage')
        self.environment['FAKE_KEYCHAIN_STATUS'] = '0'
        result = self.run_helper('claude-usage', '--fresh')
        self.assertEqual(usage_report(result.stdout)['error'], 'no login')
        self.assertEqual(self.calls.read_text(), 'security find-generic-password -s Claude Code-credentials -w\n')

    def test_unresponsive_credential_read_stops(self) -> None:
        self.environment['FAKE_SECURITY_DELAY'] = '30'
        result = subprocess.run([str(self.bin / 'claude-usage')], env=self.environment,
                                text=True, capture_output=True, timeout=12, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'keychain unavailable')
        self.assertIn('security exit=124', (self.home / '.claude/acl-events.log').read_text())

    def test_concurrent_locked_usage_refreshes_skip_credentials(self) -> None:
        self.environment['FAKE_KEYCHAIN_STATUS'] = '1'
        requests = [subprocess.Popen([str(self.bin / 'claude-usage'), '--async'],
                    env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(8)]
        for request in requests:
            request.communicate(timeout=5)
            self.assertEqual(request.returncode, 0)
        deadline = time.monotonic() + 4
        while (self.root / 'usage.fetch').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse((self.root / 'usage.fetch').exists())
        self.assertEqual(usage_report(self.cache.read_text())['error'], 'keychain unavailable')
        self.assertFalse(self.calls.exists())

    def test_concurrent_pr_refreshes_share_one_request(self) -> None:
        self.environment['FAKE_DELAY'] = '0.3'
        requests = [subprocess.Popen([str(self.bin / 'gh-pr-lookup'), 'example', 'main', '--async'],
                    env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(8)]
        for request in requests:
            output, _ = request.communicate(timeout=5)
            self.assertIn(output, ['!\tchecking\n', '!\tlogin required\n'])
        self.assertIn('__LOGIN__', self.wait_for_map())
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_successful_pr_and_no_pr_are_distinct_from_failure(self) -> None:
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='42\tImprove widgets\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'feature').stdout, '42\tImprove widgets\n')
        self.environment['FAKE_RESULT'] = ''
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'main').stdout, '')

    def test_abandoned_write_lock_does_not_freeze_the_cache(self) -> None:
        cache_dir = self.home / '.cache'
        cache_dir.mkdir()
        lock = cache_dir / 'gh-pr-map.lock'
        lock.mkdir()
        stale = time.time() - 120
        os.utime(lock, (stale, stale))
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='7\tRecover locks\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'locks').stdout, '7\tRecover locks\n')
        self.assertIn('example:locks\t7\tRecover locks', self.wait_for_map())
        self.assertFalse(lock.exists())

    def test_live_write_lock_is_respected(self) -> None:
        cache_dir = self.home / '.cache'
        cache_dir.mkdir()
        (cache_dir / 'gh-pr-map.lock').mkdir()
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='7\tRecover locks\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'locks').stdout, '7\tRecover locks\n')
        self.assertFalse((cache_dir / 'gh-pr-map').exists())

    def test_usage_reads_login_through_security_tool(self) -> None:
        self.environment.update(FAKE_SECURITY_STATUS='0', FAKE_SECRET=EXPIRED_LOGIN)
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'token expired')
        self.assertEqual(self.calls.read_text(), 'security find-generic-password -s Claude Code-credentials -w\n')

    def test_usage_failure_persists_and_returns_failure_on_cached_read(self) -> None:
        self.cache.write_text(json.dumps({'ok': True, 'fable': 23, 'fetched_at': 0}))
        first = self.run_helper('claude-usage')
        self.assertEqual(first.returncode, 1)
        result = usage_report(first.stdout)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'no login')
        self.assertEqual(result['fable'], 23)
        second = self.run_helper('claude-usage')
        self.assertEqual(second.returncode, 1)
        self.assertFalse(usage_report(second.stdout)['ok'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_usage_failure_without_cache_is_saved(self) -> None:
        self.environment['FAKE_SECURITY_STATUS'] = '36'
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(self.cache.read_text())['error'], 'keychain unavailable')

    def test_stale_credentials_file_is_ignored(self) -> None:
        credentials = self.home / '.claude/.credentials.json'
        credentials.parent.mkdir()
        credentials.write_text(EXPIRED_LOGIN)
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'no login')

    def test_pr_status_serves_cached_data_on_not_modified(self) -> None:
        cache_dir = self.home / '.cache/gh-pr-etag'
        cache_dir.mkdir(parents=True)
        (cache_dir / 'example_project_42_pr').write_text(json.dumps({'state': 'closed', 'merged': True}))
        (cache_dir / 'example_project_42_pr.etag').write_text('"abc"')
        self.environment.update(FAKE_STATUS='1', FAKE_RESULT='HTTP/2.0 304 Not Modified\r\n\r\n', FAKE_STDERR='gh: HTTP 304\n')
        self.assertEqual(self.run_helper('gh-pr-status', 'example/project', '42').stdout, 'merged:pass:ok\n')

    def test_pr_status_reports_credential_failure(self) -> None:
        self.assertEqual(self.run_helper('gh-pr-status', 'example/project', '42').stdout, '!\tlogin required\n')

    def test_git_status_renders_notice_without_invalid_pr_link(self) -> None:
        for name, body in {
            'git-meta': 'printf "example\\texample/project\\tfeature\\n"',
            'gh-pr-lookup': 'printf "!\\tlogin required\\n"',
            'gt-status': 'exit 0',
            'git': 'if [[ "$1" = rev-parse ]]; then exit 1; fi',
            'gh-pr-status': 'exit 99',
        }.items():
            self.install(name, '#!/bin/bash\n' + body + '\n')
        result = self.run_helper('git-status-line')
        self.assertIn('[PR: login required]', result.stdout)
        self.assertNotIn('#!', result.stdout)
        self.assertNotIn('https://', result.stdout)

    def run_statusline(self, usage_json: str, payload: JSONValue) -> str:
        self.install('claude-usage', '#!/bin/bash\nprintf \'%s\\n\' \'' + usage_json + '\'\n')
        source = (REPOSITORY / '.claude/statusline.sh').read_text()
        source = source.replace(file_reference(source, 'claude-rate-limits.json'), str(self.root / 'rates.json'))
        statusline = self.root / 'statusline.sh'
        statusline.write_text(source)
        self.environment['HIDE_GIT_PROMPT'] = '1'
        return subprocess.run(['bash', str(statusline)], input=json.dumps(payload), env=self.environment,
                              capture_output=True, text=True, timeout=5, check=False).stdout

    def test_usage_statusline_preserves_provider_rates_and_shows_login_notice(self) -> None:
        payload: JSONValue = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000},
                   'rate_limits': {'five_hour': {'used_percentage': 20}, 'seven_day': {'used_percentage': 30}}}
        output = self.run_statusline('{"ok":false,"error":"no login","fable":23}', payload)
        self.assertIn('Fable: login required', output)
        self.assertNotIn('Fable 23%', output)
        self.assertIn('5h ', output)
        self.assertIn('7d ', output)

    def test_statusline_has_no_dangling_separator_with_one_usage_part(self) -> None:
        payload: JSONValue = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000}}
        output = self.run_statusline('{"ok":false,"error":"no login"}', payload)
        usage_line = next(line for line in output.splitlines() if 'Usage' in line)
        self.assertTrue(usage_line.endswith('Fable: login required\x1b[0m'), usage_line)


    def test_cached_result_is_served_for_five_minutes(self) -> None:
        payload = {'ok': False, 'error': 'HTTP 429', 'fetched_at': int(time.time()) - 200}
        self.cache.write_text(json.dumps(payload))
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'HTTP 429')
        self.assertFalse(self.calls.exists())

    def test_stale_success_without_activity_serves_cache(self) -> None:
        payload = {'ok': True, 'fable': 42, 'five_hour': 20, 'seven_day': 30,
                   'fetched_at': int(time.time()) - 400}
        self.cache.write_text(json.dumps(payload))
        environment = dict(self.environment, STATUSLINE_5H='20', STATUSLINE_7D='30')
        result = subprocess.run([str(self.bin / 'claude-usage')], env=environment,
                                text=True, capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_stale_success_with_activity_refetches(self) -> None:
        payload = {'ok': True, 'fable': 42, 'five_hour': 20, 'seven_day': 30,
                   'fetched_at': int(time.time()) - 400}
        self.cache.write_text(json.dumps(payload))
        environment = dict(self.environment, STATUSLINE_5H='23', STATUSLINE_7D='30')
        subprocess.run([str(self.bin / 'claude-usage')], env=environment,
                       text=True, capture_output=True, timeout=5, check=False)
        self.assertIn('security find-generic-password', self.calls.read_text())

    def test_heartbeat_refetches_after_thirty_minutes_of_idle(self) -> None:
        payload = {'ok': True, 'fable': 42, 'five_hour': 20, 'seven_day': 30,
                   'fetched_at': int(time.time()) - 2000}
        self.cache.write_text(json.dumps(payload))
        environment = dict(self.environment, STATUSLINE_5H='20', STATUSLINE_7D='30')
        subprocess.run([str(self.bin / 'claude-usage')], env=environment,
                       text=True, capture_output=True, timeout=5, check=False)
        self.assertIn('security find-generic-password', self.calls.read_text())

    def test_acl_failure_logs_without_desktop_notifications(self) -> None:
        self.environment['FAKE_SECURITY_STATUS'] = '36'
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(usage_report(result.stdout)['error'], 'keychain unavailable')
        log = self.home / '.claude/acl-events.log'
        self.assertTrue(log.exists())
        self.assertIn('security exit=36', log.read_text().splitlines()[-1])
        self.assertEqual(self.calls.read_text(), 'security find-generic-password -s Claude Code-credentials -w\n')

    def test_statusline_labels_rate_limit_distinctly(self) -> None:
        payload: JSONValue = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000},
                   'rate_limits': {'five_hour': {'used_percentage': 20}, 'seven_day': {'used_percentage': 30}}}
        output = self.run_statusline('{"ok":false,"error":"HTTP 429","fable":73}', payload)
        self.assertIn('Fable 73% · rate limited', output)


if __name__ == '__main__':
    unittest.main()

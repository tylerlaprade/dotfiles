import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]


class BackgroundHelpersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'scripts/bin'
        self.bin.mkdir(parents=True)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.environment = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:{os.environ["PATH"]}')
        self.cache = self.root / 'claude-usage.json'
        for name in ['gh-background', 'gh-pr-lookup', 'gh-pr-status', 'git-status-line', 'claude-usage']:
            source = (REPOSITORY / f'scripts/bin/{name}.sh').read_text()
            source = source.replace('/tmp/claude-usage.json', str(self.cache)).replace('/tmp/claude-usage.fetch', str(self.root / 'usage.fetch'))
            self.install(name, source)
        self.calls = self.root / 'calls'
        self.install('gh', '#!/bin/bash\n'
                     f'printf "%s\\n" "$*" >> {str(self.calls)!r}\n'
                     'sleep "${FAKE_DELAY:-0}"\n'
                     'printf "%s" "${FAKE_RESULT:-}"\n'
                     'printf "%s" "${FAKE_STDERR:-}" >&2\n'
                     'exit "${FAKE_STATUS:-4}"\n')
        self.install('security', '#!/bin/bash\n'
                     f'printf "security %s\\n" "$*" >> {str(self.calls)!r}\n'
                     'printf "%s" "${FAKE_SECRET:-}"\n'
                     'exit "${FAKE_SECURITY_STATUS:-44}"\n')

    def install(self, name, source):
        target = self.bin / name
        target.write_text(source)
        target.chmod(0o755)

    def run_helper(self, name, *arguments, **options):
        return subprocess.run([str(self.bin / name), *arguments], env=self.environment,
                              text=True, capture_output=True, timeout=5, **options)

    def wait_for_map(self):
        deadline = time.monotonic() + 4
        while not (self.home / '.cache/gh-pr-map').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return (self.home / '.cache/gh-pr-map').read_text()

    def test_gh_runs_without_prompts_and_classifies_failures(self):
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 11)
        self.environment.update(FAKE_STATUS='1', FAKE_STDERR='gh: Bad credentials (HTTP 401)\n')
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 11)
        self.environment.update(FAKE_STATUS='1', FAKE_STDERR='gh: Not Found (HTTP 404)\n')
        self.assertEqual(self.run_helper('gh-background', 'api', 'user').returncode, 12)
        self.environment.update(FAKE_STATUS='0', FAKE_STDERR='', FAKE_RESULT='{"login":"Tyler"}')
        result = self.run_helper('gh-background', 'api', 'user')
        self.assertEqual((result.returncode, result.stdout), (0, '{"login":"Tyler"}'))

    def test_pr_failure_is_cached_and_visible(self):
        first = self.run_helper('gh-pr-lookup', 'example', 'feature/[test]')
        self.assertEqual(first.stdout, '!\tlogin required\n')
        second = self.run_helper('gh-pr-lookup', 'example', 'feature/[test]', '--async')
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_concurrent_pr_refreshes_share_one_request(self):
        self.environment['FAKE_DELAY'] = '0.3'
        requests = [subprocess.Popen([str(self.bin / 'gh-pr-lookup'), 'example', 'main', '--async'],
                    env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(8)]
        for request in requests:
            output, _ = request.communicate(timeout=5)
            self.assertIn(output, ['!\tchecking\n', '!\tlogin required\n'])
        self.assertIn('__LOGIN__', self.wait_for_map())
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_successful_pr_and_no_pr_are_distinct_from_failure(self):
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='42\tImprove widgets\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'feature').stdout, '42\tImprove widgets\n')
        self.environment['FAKE_RESULT'] = ''
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'main').stdout, '')

    def test_abandoned_write_lock_does_not_freeze_the_cache(self):
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

    def test_live_write_lock_is_respected(self):
        cache_dir = self.home / '.cache'
        cache_dir.mkdir()
        (cache_dir / 'gh-pr-map.lock').mkdir()
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='7\tRecover locks\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'locks').stdout, '7\tRecover locks\n')
        self.assertFalse((cache_dir / 'gh-pr-map').exists())

    def test_usage_reads_login_through_security_tool(self):
        self.environment.update(FAKE_SECURITY_STATUS='0', FAKE_SECRET=json.dumps(
            {'claudeAiOauth': {'accessToken': 'example-token', 'expiresAt': 1600000000000}}))
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'token expired')
        self.assertEqual(self.calls.read_text(), 'security find-generic-password -s Claude Code-credentials -w\n')

    def test_usage_failure_persists_and_returns_failure_on_cached_read(self):
        self.cache.write_text(json.dumps({'ok': True, 'fable': 23, 'fetched_at': 0}))
        first = self.run_helper('claude-usage')
        self.assertEqual(first.returncode, 1)
        result = json.loads(first.stdout)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'no login')
        self.assertEqual(result['fable'], 23)
        second = self.run_helper('claude-usage')
        self.assertEqual(second.returncode, 1)
        self.assertFalse(json.loads(second.stdout)['ok'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_usage_failure_without_cache_is_saved(self):
        self.environment['FAKE_SECURITY_STATUS'] = '36'
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(self.cache.read_text())['error'], 'keychain unavailable')

    def test_stale_credentials_file_is_ignored(self):
        credentials = self.home / '.claude/.credentials.json'
        credentials.parent.mkdir()
        credentials.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'example-token', 'expiresAt': 1600000000000}}))
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'no login')

    def test_pr_status_serves_cached_data_on_not_modified(self):
        cache_dir = self.home / '.cache/gh-pr-etag'
        cache_dir.mkdir(parents=True)
        (cache_dir / 'example_project_42_pr').write_text(json.dumps({'state': 'closed', 'merged': True}))
        (cache_dir / 'example_project_42_pr.etag').write_text('"abc"')
        self.environment.update(FAKE_STATUS='1', FAKE_RESULT='HTTP/2.0 304 Not Modified\r\n\r\n', FAKE_STDERR='gh: HTTP 304\n')
        self.assertEqual(self.run_helper('gh-pr-status', 'example/project', '42').stdout, 'merged:pass:ok\n')

    def test_pr_status_reports_credential_failure(self):
        self.assertEqual(self.run_helper('gh-pr-status', 'example/project', '42').stdout, '!\tlogin required\n')

    def test_git_status_renders_notice_without_invalid_pr_link(self):
        for name, body in {
            'git-meta': 'printf "example\\texample/project\\tfeature\\n"',
            'gh-pr-lookup': 'printf "!\\tlogin required\\n"',
            'gt-status': 'exit 0',
            'git': 'if [[ "$1" = rev-list ]]; then echo "0 0"; fi',
            'gh-pr-status': 'exit 99',
        }.items():
            self.install(name, '#!/bin/bash\n' + body + '\n')
        result = self.run_helper('git-status-line')
        self.assertIn('[PR: login required]', result.stdout)
        self.assertNotIn('#!', result.stdout)
        self.assertNotIn('https://', result.stdout)

    def run_statusline(self, usage_json, payload):
        self.install('claude-usage', '#!/bin/bash\nprintf \'%s\\n\' \'' + usage_json + '\'\n')
        source = (REPOSITORY / '.claude/statusline.sh').read_text().replace('/tmp/claude-rate-limits.json', str(self.root / 'rates.json'))
        statusline = self.root / 'statusline.sh'
        statusline.write_text(source)
        self.environment['HIDE_GIT_PROMPT'] = '1'
        return subprocess.run(['bash', str(statusline)], input=json.dumps(payload), env=self.environment,
                              capture_output=True, text=True, timeout=5).stdout

    def test_usage_statusline_preserves_provider_rates_and_shows_login_notice(self):
        payload = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000},
                   'rate_limits': {'five_hour': {'used_percentage': 20}, 'seven_day': {'used_percentage': 30}}}
        output = self.run_statusline('{"ok":false,"error":"no login","fable":23}', payload)
        self.assertIn('Fable: login required', output)
        self.assertNotIn('Fable 23%', output)
        self.assertIn('5h ', output)
        self.assertIn('7d ', output)

    def test_statusline_has_no_dangling_separator_with_one_usage_part(self):
        payload = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000}}
        output = self.run_statusline('{"ok":false,"error":"no login"}', payload)
        usage_line = next(line for line in output.splitlines() if 'Usage' in line)
        self.assertTrue(usage_line.endswith('Fable: login required\x1b[0m'), usage_line)


if __name__ == '__main__':
    unittest.main()

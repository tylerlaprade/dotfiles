import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


REPOSITORY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('background_auth', REPOSITORY / 'scripts/lib/background_auth.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class BackgroundHelpersTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'scripts/bin'
        self.lib = self.root / 'scripts/lib'
        self.bin.mkdir(parents=True)
        self.lib.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.environment = dict(os.environ, HOME=str(self.home), PATH=f'{self.bin}:{os.environ["PATH"]}')
        self.cache = self.root / 'claude-usage.json'
        for name in ['gh-pr-lookup', 'gh-pr-status', 'git-status-line', 'claude-usage']:
            source = (REPOSITORY / f'scripts/bin/{name}.sh').read_text()
            source = source.replace('/tmp/claude-usage.json', str(self.cache)).replace('/tmp/claude-usage.fetch', str(self.root / 'usage.fetch'))
            target = self.bin / name
            target.write_text(source)
            target.chmod(0o755)
        self.calls = self.root / 'calls'
        self.lib.joinpath('background_auth.py').write_text(
            'import os,sys,time\n'
            f'with open({str(self.calls)!r}, "a") as f: f.write(" ".join(sys.argv[1:])+"\\n")\n'
            'time.sleep(float(os.environ.get("FAKE_DELAY", "0")))\n'
            'sys.stdout.write(os.environ.get("FAKE_RESULT", ""))\n'
            'sys.exit(int(os.environ.get("FAKE_STATUS", "10")))\n'
        )

    def run_helper(self, name, *arguments, **options):
        return subprocess.run([str(self.bin / name), *arguments], env=self.environment,
                              text=True, capture_output=True, timeout=5, **options)

    def test_pr_failure_is_cached_and_visible(self):
        first = self.run_helper('gh-pr-lookup', 'example', 'feature/[test]')
        self.assertEqual(first.stdout, '!\tkeychain unavailable\n')
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
            self.assertIn(output, ['!\tchecking\n', '!\tkeychain unavailable\n'])
        deadline = time.monotonic() + 4
        while not (self.home / '.cache/gh-pr-map').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertIn('__KEYCHAIN__', (self.home / '.cache/gh-pr-map').read_text())

    def test_successful_pr_and_no_pr_are_distinct_from_failure(self):
        self.environment.update(FAKE_STATUS='0', FAKE_RESULT='42\tImprove widgets\n')
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'feature').stdout, '42\tImprove widgets\n')
        self.environment['FAKE_RESULT'] = ''
        self.assertEqual(self.run_helper('gh-pr-lookup', 'example', 'main').stdout, '')

    def test_usage_failure_persists_and_returns_failure_on_cached_read(self):
        self.cache.write_text(json.dumps({'ok': True, 'fable': 23, 'fetched_at': 0}))
        first = self.run_helper('claude-usage')
        self.assertEqual(first.returncode, 1)
        result = json.loads(first.stdout)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'keychain unavailable')
        self.assertEqual(result['fable'], 23)
        second = self.run_helper('claude-usage')
        self.assertEqual(second.returncode, 1)
        self.assertFalse(json.loads(second.stdout)['ok'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_usage_failure_without_cache_is_saved(self):
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(self.cache.read_text())['error'], 'keychain unavailable')

    def test_expired_fallback_does_not_hide_keychain_denial(self):
        credentials = self.home / '.claude/.credentials.json'
        credentials.parent.mkdir()
        credentials.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'example-token', 'expiresAt': 1600000000000}}))
        result = self.run_helper('claude-usage')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'keychain unavailable')

    def test_pr_status_reports_credential_failure(self):
        self.assertEqual(self.run_helper('gh-pr-status', 'example/project', '42').stdout, '!\tkeychain unavailable\n')

    def test_git_status_renders_notice_without_invalid_pr_link(self):
        for name, body in {
            'git-meta': 'printf "example\\texample/project\\tfeature\\n"',
            'gh-pr-lookup': 'printf "!\\tkeychain unavailable\\n"',
            'gt-status': 'exit 0',
            'git': 'if [[ "$1" = rev-list ]]; then echo "0 0"; fi',
            'gh-pr-status': 'exit 99',
        }.items():
            file = self.bin / name
            file.write_text('#!/bin/bash\n' + body + '\n')
            file.chmod(0o755)
        result = self.run_helper('git-status-line')
        self.assertIn('[PR: keychain unavailable]', result.stdout)
        self.assertNotIn('#!', result.stdout)
        self.assertNotIn('https://', result.stdout)

    def test_usage_statusline_preserves_provider_rates_and_shows_keychain_notice(self):
        file = self.bin / 'claude-usage'
        file.write_text('#!/bin/bash\nprintf \'%s\\n\' \'{"ok":false,"error":"keychain unavailable","fable":23}\'\n')
        source = (REPOSITORY / '.claude/statusline.sh').read_text().replace('/tmp/claude-rate-limits.json', str(self.root / 'rates.json'))
        statusline = self.root / 'statusline.sh'
        statusline.write_text(source)
        self.environment['HIDE_GIT_PROMPT'] = '1'
        payload = {'workspace': {'current_dir': str(self.root)}, 'context_window': {'total_input_tokens': 1000, 'context_window_size': 200000},
                   'rate_limits': {'five_hour': {'used_percentage': 20}, 'seven_day': {'used_percentage': 30}}}
        result = subprocess.run(['bash', str(statusline)], input=json.dumps(payload), env=self.environment,
                                capture_output=True, text=True, timeout=5)
        self.assertIn('Fable: keychain unavailable', result.stdout)
        self.assertNotIn('Fable 23%', result.stdout)
        self.assertIn('5h ', result.stdout)
        self.assertIn('7d ', result.stdout)


class CredentialReaderTest(unittest.TestCase):
    def test_native_reader_refuses_interaction_before_access(self):
        security = Mock()
        security.SecKeychainSetUserInteractionAllowed.return_value = 0
        security.SecKeychainFindGenericPassword.return_value = -25308
        with patch.object(auth.ctypes, 'CDLL', return_value=security):
            with self.assertRaises(auth.CredentialError) as caught:
                auth.read_keychain('example')
        self.assertEqual(caught.exception.exit_code, auth.KEYCHAIN_UNAVAILABLE)
        security.SecKeychainSetUserInteractionAllowed.assert_called_once_with(False)
        self.assertEqual(security.mock_calls[0][0], 'SecKeychainSetUserInteractionAllowed')
        self.assertEqual(security.mock_calls[1][0], 'SecKeychainFindGenericPassword')

    def test_native_reader_returns_authorized_data_and_frees_it(self):
        security = Mock()
        security.SecKeychainSetUserInteractionAllowed.return_value = 0
        data = ctypes.create_string_buffer(b'example-token')
        def found(keychain, service_length, service, account_length, account, length, pointer, item):
            length._obj.value = len(b'example-token')
            pointer._obj.value = ctypes.addressof(data)
            return 0
        security.SecKeychainFindGenericPassword.side_effect = found
        with patch.object(auth.ctypes, 'CDLL', return_value=security):
            self.assertEqual(auth.read_keychain('example', 'Tyler'), 'example-token')
        security.SecKeychainItemFreeContent.assert_called_once()

    def test_blocked_keychain_never_falls_back_to_interactive_security(self):
        result = subprocess.CompletedProcess([], auth.KEYCHAIN_UNAVAILABLE, '', 'keychain unavailable')
        with patch.object(auth.subprocess, 'run', return_value=result) as run:
            with self.assertRaises(auth.CredentialError) as caught:
                auth.keychain_password('example')
        self.assertEqual(caught.exception.exit_code, auth.KEYCHAIN_UNAVAILABLE)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('security', run.call_args.args[0])

    def test_timed_out_keychain_reports_unavailable(self):
        with patch.object(auth.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reader', 3)):
            with self.assertRaises(auth.CredentialError) as caught:
                auth.keychain_password('example')
        self.assertEqual(caught.exception.exit_code, auth.KEYCHAIN_UNAVAILABLE)

    def test_explicit_token_does_not_touch_keychain(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'example-token'}), patch.object(auth, 'keychain_password') as read:
            self.assertEqual(auth.github_token(), 'example-token')
        read.assert_not_called()


if __name__ == '__main__':
    unittest.main()

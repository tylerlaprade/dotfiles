import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
MODELS = ['gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna']
ROUTES = {
    'ANTHROPIC_MODEL': 'gpt-6-astra',
    'ANTHROPIC_DEFAULT_FABLE_MODEL': 'gpt-6-astra',
    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'gpt-5.6-sol',
    'ANTHROPIC_DEFAULT_SONNET_MODEL': 'gpt-5.6-terra',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'gpt-5.6-luna',
}


class ClaudexTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.cache = self.root / '.codex/models_cache.json'
        self.cache.parent.mkdir()
        self.environment = dict(os.environ, HOME=str(self.root),
                                PATH=f'{self.bin}:{os.environ["PATH"]}',
                                ANTHROPIC_API_KEY='test-only-key')
        self.environment.update({name: 'inherited-model' for name in ROUTES})
        probe = self.bin / 'claude'
        probe.write_text(f'#!{sys.executable}\n'
                         'import json, os, sys\n'
                         'print(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}))\n'
                         'sys.exit(int(os.environ.get("PROBE_EXIT", "0")))\n')
        probe.chmod(0o755)
        self.rows = [{'slug': model, 'context_window': 272000, 'max_context_window': 872000}
                     for model in MODELS]
        self.save_cache()

    def save_cache(self):
        self.cache.write_text(json.dumps({'models': self.rows}))

    def launch(self, *arguments, launcher=None):
        launcher = launcher or REPOSITORY / 'scripts/bin/claudex.sh'
        return subprocess.run([str(launcher), *arguments], env=self.environment,
                              text=True, capture_output=True, timeout=5)

    def inspect(self, *arguments, **options):
        result = self.launch(*arguments, **options)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_routes_match_in_wrapper_and_high_precedence_settings(self):
        result = self.inspect()
        settings = json.loads(result['argv'][1])
        for name, model in ROUTES.items():
            self.assertEqual(result['env'][name], model)
            self.assertEqual(settings['env'][name], model)
        self.assertNotIn('ANTHROPIC_API_KEY', result['env'])
        self.assertEqual(result['env']['ANTHROPIC_CUSTOM_MODEL_OPTION'], MODELS[0])
        self.assertEqual(result['env']['ANTHROPIC_CUSTOM_MODEL_OPTION_NAME'], 'GPT-6 Astra')

    def test_main_cache_ttl_does_not_override_subagents(self):
        settings = json.loads(self.inspect()['argv'][1])
        self.assertEqual(settings['promptCacheTtl'], '1h')
        self.assertNotIn('subagentPromptCacheTtl', settings)

    def test_picker_and_isolation(self):
        settings = json.loads(self.inspect()['argv'][1])
        self.assertEqual(settings['feedbackDrafts'], 'off')
        self.assertFalse(settings.get('ultracode', False))
        self.assertEqual(settings['env']['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'], '')
        self.assertEqual(settings['permissions']['deny'], ['ListAgents', 'SendMessage'])
        self.assertTrue(settings['modelPicker']['replaceBuiltInOptions'])
        self.assertEqual([row['model'] for row in settings['modelPicker']['options']], MODELS)
        self.assertEqual([row['label'] for row in settings['modelPicker']['options']],
                         ['GPT-6 Astra', 'GPT-5.6 Sol', 'GPT-5.6 Terra', 'GPT-5.6 Luna'])

    def test_context_uses_common_maximum_with_compaction_headroom(self):
        env = self.inspect()['env']
        self.assertEqual(env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '872000')
        self.assertEqual(env['CLAUDE_CODE_AUTO_COMPACT_WINDOW'], '828400')
        self.rows[-1]['max_context_window'] = 400000
        self.save_cache()
        env = self.inspect()['env']
        self.assertEqual(env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '400000')
        self.assertEqual(env['CLAUDE_CODE_AUTO_COMPACT_WINDOW'], '380000')

    def test_context_without_maximum_uses_default(self):
        for row in self.rows:
            del row['max_context_window']
        self.save_cache()
        env = self.inspect()['env']
        self.assertEqual(env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '272000')
        self.assertEqual(env['CLAUDE_CODE_AUTO_COMPACT_WINDOW'], '258400')

    def test_bad_or_missing_cache_uses_conservative_fallback(self):
        cases = [None, '{', json.dumps({'models': self.rows[:-1]}),
                 json.dumps({'models': self.rows + [self.rows[0]]})]
        for value in (0, -1, 872000.5, 1000001, '872000'):
            rows = [dict(row) for row in self.rows]
            rows[0]['max_context_window'] = value
            cases.append(json.dumps({'models': rows}))
        for cache in cases:
            with self.subTest(cache=cache):
                if cache is None:
                    self.cache.unlink(missing_ok=True)
                else:
                    self.cache.write_text(cache)
                env = self.inspect()['env']
                self.assertEqual(env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'], '272000')
                self.assertEqual(env['CLAUDE_CODE_AUTO_COMPACT_WINDOW'], '258400')

    def test_symlink_arguments_and_exit_status(self):
        launcher = self.bin / 'claudex'
        launcher.symlink_to(REPOSITORY / 'scripts/bin/claudex.sh')
        result = self.inspect('--model', 'gpt-5.6-luna', 'a spaced prompt', launcher=launcher)
        self.assertEqual(result['argv'][2:], ['--model', 'gpt-5.6-luna', 'a spaced prompt'])
        settings = json.loads(result['argv'][1])
        self.assertEqual(Path(settings['processWrapper']), REPOSITORY / 'scripts/bin/claudex-env.sh')
        self.environment['PROBE_EXIT'] = '17'
        self.assertEqual(self.launch(launcher=launcher).returncode, 17)


if __name__ == '__main__':
    unittest.main()

"""Version-independent transport preflight; no network or production config."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile


def check():
    module = runpy.run_path(str(Path(__file__).with_name("gbb-multica-agent")))
    transport = module["GBB_TRANSPORT"]
    with tempfile.TemporaryDirectory(prefix="gbb-compat-test-") as tmp:
        root = Path(tmp)
        (root / "package.json").write_text('{"type":"module"}')
        (root / "cli.js").write_text('// fixture entrypoint\n')
        config = '''
export function loadConfig() { return {defaultBot: 'bridge'}; }
export function getCredentials() { return {url: 'https://example.invalid/private', key: 'NEVER_PRINT_KEY'}; }
'''
        (root / "config.js").write_text(config)
        (root / "messages.js").write_text('''
export function newMessage() { throw new Error('MUST_NOT_CREATE_MESSAGE'); }
export function waitForAnswer() { throw new Error('MUST_NOT_WAIT'); }
''')
        webhook = '''
export const VERSION = 'VERSION_PLACEHOLDER';
export function buildPayload() { throw new Error('MUST_NOT_BUILD_PAYLOAD'); }
export function send() { throw new Error('MUST_NOT_SEND'); }
'''
        def run():
            return subprocess.run(['node', '-e', transport, str(root / 'cli.js')],
                input=json.dumps({'check': True}), text=True, capture_output=True,
                env={'PATH': os.environ['PATH'], 'GBB_HOME': str(root / 'store')}, timeout=10)
        for version in ('0.1.0', '0.2.0', '0.2.7'):
            (root / 'webhook.js').write_text(webhook.replace('VERSION_PLACEHOLDER', version))
            result = run()
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)['gbb_version'] == version
            assert 'NEVER_PRINT_KEY' not in result.stdout + result.stderr
            assert not (root / 'store').exists(), 'preflight must not write store files'
        (root / 'webhook.js').write_text("export const VERSION='0.3.0';\n")
        missing = run()
        assert missing.returncode == 1 and 'module-api:missing=send,buildPayload' in missing.stderr
        (root / 'webhook.js').write_text(webhook.replace('VERSION_PLACEHOLDER', '0.2.0'))
        (root / 'config.js').write_text(config.replace("return {defaultBot: 'bridge'};",
            "throw new Error('NEVER_PRINT_KEY https://example.invalid/private');"))
        failed = run()
        assert failed.returncode == 1 and 'stage=config-load' in failed.stderr
        assert 'NEVER_PRINT_KEY' not in failed.stderr and 'https://' not in failed.stderr
        (root / 'config.js').unlink()
        missing_module = run()
        assert missing_module.returncode == 1 and 'stage=module-import:config.js' in missing_module.stderr
        assert 'ERR_MODULE_NOT_FOUND' in missing_module.stderr
    print('PASS: compatible version strings, missing API/module diagnostics, no-send/no-write preflight, secret-safe failures')


if __name__ == '__main__':
    check()

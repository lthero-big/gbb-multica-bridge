"""Update real local Git repositories; no GitHub, Bots or Multica API calls."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
INSTALL = runpy.run_path(str(ROOT / 'install.py'))
AGENT = runpy.run_path(str(ROOT / 'gbb-multica-agent'))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gbb-update-test-')
        self.root = Path(self.tmp.name)
        self.origin, self.seed, self.checkout = (self.root / name for name in ('origin.git', 'seed', 'checkout'))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('MULTICA_', 'GBB_'))}
        self.env.update(GIT_AUTHOR_NAME='Offline Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Offline Test', GIT_COMMITTER_EMAIL='test@example.invalid',
                        UPDATE_TEST_LOG=str(self.root / 'reinstall.log'))
        self.patch_env = patch.dict(os.environ, self.env, clear=True)
        self.patch_env.start()
        self.git(self.root, 'init', '--bare', '--initial-branch=main', str(self.origin))
        self.git(self.root, 'init', '--initial-branch=main', str(self.seed))
        (self.seed / 'install.py').write_text(
            "import os,sys\nwith open(os.environ['UPDATE_TEST_LOG'],'a') as f: f.write(repr(sys.argv[1:])+'\\n')\n")
        shutil.copy2(ROOT / 'gbb-multica-agent', self.seed / 'gbb-multica-agent')
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-m', 'initial')
        self.git(self.seed, 'remote', 'add', 'origin', str(self.origin))
        self.git(self.seed, 'push', 'origin', 'main')
        self.git(self.root, 'clone', str(self.origin), str(self.checkout))
        self.old = self.git(self.checkout, 'rev-parse', 'HEAD')
        (self.seed / 'release-marker').write_text('new release')
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-m', 'next release')
        self.git(self.seed, 'push', 'origin', 'main')
        self.latest = self.git(self.seed, 'rev-parse', 'HEAD')
        self.config = self.root / 'config'
        self.config.mkdir()
        self.home = self.root / 'store'
        (self.config / 'server.json').write_text(json.dumps({
            'instance_id': 'original-instance', 'gbb_home': str(self.home),
            'bridge_version': '0.5.1', 'source_commit': self.old}))
        (self.config / 'routes.json').write_text(json.dumps({'routes': {'local-route': {}, 'ssh-route': {}}}))
        self.patch_globals = patch.dict(INSTALL['update'].__globals__, ROOT=self.checkout, UPSTREAM_URLS=(str(self.origin),))
        self.patch_globals.start()

    def tearDown(self):
        self.patch_globals.stop()
        self.patch_env.stop()
        self.tmp.cleanup()

    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True,
                              env=self.env, check=True, timeout=10).stdout.strip()

    def update(self, check=False):
        output = io.StringIO()
        with redirect_stdout(output):
            result = INSTALL['update'](argparse.Namespace(check=check), self.config, self.root / 'bin')
        self.assertEqual(result, 0)
        return output.getvalue()

    def test_check_is_read_only_and_reports_installed_version(self):
        refs = self.git(self.checkout, 'show-ref')
        output = self.update(check=True)
        self.assertIn('0.5.1', output)
        self.assertIn(self.latest[:12], output)
        self.assertEqual(self.git(self.checkout, 'show-ref'), refs)
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.old)
        self.assertFalse((self.root / 'reinstall.log').exists())
        self.assertFalse(self.home.exists())

    def test_fast_forward_reenters_new_installer_for_saved_roles(self):
        configs = {p.name: p.read_bytes() for p in self.config.iterdir()}
        self.update()
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.latest)
        commands = (self.root / 'reinstall.log').read_text().splitlines()
        self.assertEqual(commands, ["['runtime', '--instance-id', 'original-instance']",
                                    "['reply', '--route', 'local-route']", "['reply', '--route', 'ssh-route']"])
        self.assertEqual({p.name: p.read_bytes() for p in self.config.iterdir()}, configs)

    def test_dirty_checkout_and_development_branch_are_preserved(self):
        marker = self.checkout / 'my-uncommitted-work'
        marker.write_text('keep me')
        with self.assertRaisesRegex(ValueError, '仓库不干净'):
            self.update()
        self.assertEqual(marker.read_text(), 'keep me')
        marker.unlink()
        self.git(self.checkout, 'switch', '-c', 'feature')
        with self.assertRaisesRegex(ValueError, '只用于 main'):
            self.update()
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.old)
        self.assertFalse((self.root / 'reinstall.log').exists())

    def test_diverged_main_is_not_reset_or_reinstalled(self):
        (self.checkout / 'local-commit').write_text('keep my commit')
        self.git(self.checkout, 'add', '.')
        self.git(self.checkout, 'commit', '-m', 'local divergence')
        local = self.git(self.checkout, 'rev-parse', 'HEAD')
        with self.assertRaises(subprocess.CalledProcessError):
            self.update()
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), local)
        self.assertFalse((self.root / 'reinstall.log').exists())

    def test_untrusted_origin_and_task_mutation_are_rejected(self):
        self.git(self.checkout, 'remote', 'set-url', 'origin', 'https://example.invalid/wrong.git')
        with self.assertRaisesRegex(ValueError, 'origin 不是'):
            self.update(check=True)
        with patch.dict(os.environ, MULTICA_TASK_ID='running-task'):
            with self.assertRaisesRegex(ValueError, '不能在 Multica 任务'):
                self.update()

    def test_busy_store_stops_before_fetch_or_checkout_change(self):
        with AGENT['deployment_lock'](self.home):
            with self.assertRaisesRegex(ValueError, 'active task'):
                self.update()
        self.assertEqual(self.git(self.checkout, 'rev-parse', 'HEAD'), self.old)
        self.assertFalse((self.root / 'reinstall.log').exists())

    def test_legacy_journal_lock_also_blocks_update(self):
        import fcntl
        journal = self.home / 'multica-runs/old-task/lock'
        journal.parent.mkdir(parents=True)
        with journal.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, 'active task'):
                self.update()
        self.update()

    def test_management_launcher_and_dry_run(self):
        bin_dir = self.root / 'bin with spaces'
        INSTALL['install_manager'](bin_dir)
        subprocess.run([str(bin_dir / 'gbb-multica'), 'update', '--check'], env=self.env, check=True, timeout=10)
        self.assertEqual((self.root / 'reinstall.log').read_text().strip(), "['update', '--check']")
        result = subprocess.run([sys.executable, str(ROOT / 'install.py'), 'update', '--dry-run'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

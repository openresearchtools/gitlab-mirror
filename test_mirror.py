import pathlib
import tempfile
import unittest
from unittest.mock import patch
import mirror


class MirrorIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.source = str(self.root / 'source')
        self.target = str(self.root / 'target.git')
        mirror.git('init', '--initial-branch=main', self.source)
        mirror.git('init', '--bare', '--initial-branch=main', self.target)
        mirror.git('config', 'user.name', 'Mirror integration test', cwd=self.source)
        mirror.git('config', 'user.email', 'mirror-test@example.invalid', cwd=self.source)
        self.commit('first')
        self.repo = {'name': 'test', 'default_branch': 'main'}
        self.project = {'id': 1, 'default_branch': 'main'}

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, content):
        pathlib.Path(self.source, 'file.txt').write_text(content)
        mirror.git('add', 'file.txt', cwd=self.source)
        mirror.git('commit', '-m', content, cwd=self.source)

    def run_sync(self):
        return mirror.sync_repo(self.repo, self.project, self.source, self.target,
                                mirror.refs(self.source), mirror.refs(self.target))

    def test_branches_tags_force_updates_deletions_and_unchanged(self):
        mirror.git('branch', 'feature', cwd=self.source)
        mirror.git('tag', '-a', 'v1', '-m', 'annotated tag', cwd=self.source)
        self.assertEqual(self.run_sync()['status'], 'updated')
        self.assertEqual(mirror.refs(self.source), mirror.refs(self.target))
        before = mirror.git('rev-parse', 'main', cwd=self.source).strip()
        self.commit('second')
        mirror.git('branch', '-D', 'feature', cwd=self.source)
        mirror.git('tag', '-d', 'v1', cwd=self.source)
        mirror.git('tag', 'v2', cwd=self.source)
        self.run_sync()
        self.assertEqual(mirror.refs(self.source), mirror.refs(self.target))
        mirror.git('reset', '--hard', before, cwd=self.source)
        mirror.git('tag', '-f', 'v2', cwd=self.source)
        self.run_sync()
        self.assertEqual(mirror.refs(self.source), mirror.refs(self.target))
        original = mirror.git
        def checked(*args, **kwargs):
            self.assertNotIn(args[0], ('fetch', 'push', 'init'))
            return original(*args, **kwargs)
        with patch.object(mirror, 'git', side_effect=checked):
            self.assertEqual(self.run_sync()['status'], 'unchanged')

    def test_default_branch_switch_before_prune(self):
        self.run_sync()
        mirror.git('branch', '-m', 'trunk', cwd=self.source)
        self.repo['default_branch'] = 'trunk'
        def update_default(base, path, method, body):
            self.assertEqual(body, {'default_branch': 'trunk'})
            self.assertIn('refs/heads/trunk', mirror.refs(self.target))
            mirror.git('symbolic-ref', 'HEAD', 'refs/heads/trunk', cwd=self.target)
        with patch.object(mirror, 'api', side_effect=update_default):
            self.run_sync()
        self.assertEqual(mirror.refs(self.source), mirror.refs(self.target))
        self.assertNotIn('refs/heads/main', mirror.refs(self.target))

    def test_source_ref_read_failure_never_prunes(self):
        self.run_sync()
        before = mirror.refs(self.target)
        with patch.object(mirror, 'refs', side_effect=RuntimeError('network unavailable')):
            with self.assertRaises(RuntimeError):
                mirror.compare(self.repo, self.project)
        self.assertEqual(before, mirror.refs(self.target))

    def test_source_changes_during_fetch_never_push(self):
        before = mirror.refs(self.target)
        original_refs = mirror.refs
        with patch.object(mirror, 'refs', side_effect=lambda url: {'refs/heads/main': '0'*40} if url == self.source else original_refs(url)):
            with self.assertRaisesRegex(RuntimeError, 'changed during fetch'):
                mirror.sync_repo(self.repo, self.project, self.source, self.target, {'refs/heads/main': '1'*40}, {})
        self.assertEqual(before, mirror.refs(self.target))


if __name__ == '__main__':
    unittest.main()

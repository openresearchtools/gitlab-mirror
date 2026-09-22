import importlib.util
import os
import unittest
from unittest.mock import patch
import mirror

spec = importlib.util.spec_from_file_location('askpass', os.path.join(os.path.dirname(__file__), 'git-askpass.py'))
askpass = importlib.util.module_from_spec(spec)
spec.loader.exec_module(askpass)


class SecurityTests(unittest.TestCase):
    def test_credential_is_bound_to_exact_gitlab_host(self):
        with patch.dict(os.environ, {'MIRROR_GITLAB_TOKEN': 'test-only-secret'}):
            self.assertEqual(askpass.answer("Username for 'https://gitlab.com': "), 'oauth2')
            self.assertEqual(askpass.answer("Password for 'https://oauth2@gitlab.com': "), 'test-only-secret')
            for host in ['https://gitlab.com.attacker.example', 'https://gitlab.com@attacker.example',
                         'http://gitlab.com', 'https://github.com', 'https://gitlab.com:444',
                         'https://attacker:password@gitlab.com']:
                with self.subTest(host=host), self.assertRaises(ValueError):
                    askpass.answer(f"Password for '{host}': ")

    def test_redirects_cannot_forward_api_tokens(self):
        with self.assertRaisesRegex(RuntimeError, 'redirect refused'):
            mirror.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://attacker.example')

    def test_repository_name_reuse_does_not_pass_id_check(self):
        entry = {'name': 'example', 'github_id': 1, 'gitlab_id': 2}
        repo = {'id': 999, 'owner': {'id': 229047507}}
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            mirror.validate_pair(entry, repo, {})

    def test_github_or_gitlab_owner_changes_are_rejected(self):
        entry = {'name': 'example', 'github_id': 1, 'gitlab_id': 2}
        repo = {'id': 1, 'owner': {'id': 229047507}, 'full_name': 'openresearchtools/example', 'private': False}
        project = {'id': 2, 'namespace': {'id': 999}}
        with self.assertRaisesRegex(RuntimeError, 'GitLab repository identity'):
            mirror.validate_pair(entry, repo, project)

    def test_exclusions_cannot_be_overridden_by_allowlist(self):
        for name in ['apt', 'WildBuzzard', 'bashkitten', 'wildbuzzard-android', 'termux-suite', 'gitlab-mirror']:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Excluded'):
                mirror.validate_pair({'name': name}, {}, {})


if __name__ == '__main__':
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_fixture_repo import materialize


class MaterializeFixtureRepoTest(unittest.TestCase):
    def run_git(self, repo, *args):
        result = subprocess.run(['git', *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.strip()

    def test_creates_real_repo_with_tracked_and_untracked_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'

            materialize('python-review-basic', repo)

            self.assertEqual(
                self.run_git(repo, 'diff', '--name-only').splitlines(),
                ['src/calculator.py', 'src/parser.py', 'src/receipts.py', 'src/taxes.py'],
            )
            self.assertEqual(
                self.run_git(repo, 'ls-files', '--others', '--exclude-standard').splitlines(),
                ['src/formatters.py', 'src/reports.py'],
            )

    def test_writes_mock_lpr_state_inside_git_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'

            materialize('python-review-basic', repo)

            state = json.loads((repo / '.git' / 'lpr' / 'state.json').read_text())
            self.assertEqual(state['version'], 1)
            self.assertEqual(state['repo']['root'], str(repo))
            self.assertEqual(state['repo']['baseCommit'], self.run_git(repo, 'rev-parse', 'HEAD'))
            self.assertEqual([comment['id'] for comment in state['comments']], ['LPR-001', 'LPR-002', 'LPR-003', 'LPR-004'])
            self.assertEqual(state['comments'][0]['state'], 'open')
            self.assertEqual(state['comments'][1]['state'], 'superseded')
            self.assertEqual(state['comments'][2]['state'], 'open')
            self.assertEqual(state['comments'][3]['state'], 'dismissed')

    def test_refuses_to_write_into_non_empty_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            repo.mkdir()
            (repo / 'existing.txt').write_text('already here\n')

            with self.assertRaises(SystemExit):
                materialize('python-review-basic', repo)


if __name__ == '__main__':
    unittest.main()

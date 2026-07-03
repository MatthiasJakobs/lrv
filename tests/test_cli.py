import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_fixture_repo import materialize
from lpr.state import load_state
from lpr.tui import ReviewApp


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def run_lpr(self, repo, *args):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        return subprocess.run([sys.executable, '-m', 'lpr', *args], cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_status_lists_changed_files_and_comment_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'status')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/parser.py', result.stdout)
            self.assertIn('  open: 1 (LPR-001)', result.stdout)
            self.assertIn('  superseded: 1 (LPR-002)', result.stdout)

    def test_bare_command_prints_review_when_not_interactive(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('lpr review - HEAD', result.stdout)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/parser.py', result.stdout)
            self.assertIn('--- M src/parser.py ---', result.stdout)
            self.assertIn('>>> LPR-001 [open] src/parser.py:6', result.stdout)
            self.assertIn('>>> This silently changes invalid input instead of rejecting it.', result.stdout)

    def test_bare_command_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--- ?? src/formatters.py ---', result.stdout)
            self.assertIn('+def format_receipt_line(name: str, quantity: int, price: float) -> str:', result.stdout)

    def test_tui_tab_switches_focus_between_file_list_and_diff(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            self.assertEqual(app.focus, 'files')
            app.toggle_focus()
            self.assertEqual(app.focus, 'diff')
            app.toggle_focus()
            self.assertEqual(app.focus, 'files')

    def test_tui_moves_line_by_line_in_diff_focus(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            app.toggle_focus()
            app.move_down()
            app.move_down()
            self.assertEqual(app.selected, 0)
            self.assertEqual(app.diff_line, 2)
            app.move_up()
            self.assertEqual(app.diff_line, 1)

    def test_tui_file_focus_still_moves_between_files(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            app.move_down()
            self.assertEqual(app.selected, 1)
            self.assertEqual(app.diff_line, 0)

    def test_export_prints_only_open_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('# LPR Review', result.stdout)
            self.assertIn('### LPR-001 src/parser.py:6', result.stdout)
            self.assertIn('This silently changes invalid input instead of rejecting it.', result.stdout)
            self.assertIn('```diff\n@@ -2,9 +2,9 @@', result.stdout)
            self.assertIn('Do not resolve, dismiss, or clear LPR comments.', result.stdout)
            self.assertNotIn('LPR-002', result.stdout)

    def test_export_accepts_repository_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, 'export', str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:6', result.stdout)

    def test_export_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:6', result.stdout)

    def test_export_accepts_repo_option(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, '--repo', str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:6', result.stdout)

    def test_show_prints_one_comment_including_non_open_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'show', 'LPR-002')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('# LPR Comment', result.stdout)
            self.assertIn('### LPR-002 src/calculator.py:12', result.stdout)
            self.assertIn('State:\nsuperseded', result.stdout)

    def test_show_unknown_comment_exits_with_error(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'show', 'LPR-999')

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr.strip(), 'lpr: unknown comment id: LPR-999')


if __name__ == '__main__':
    unittest.main()

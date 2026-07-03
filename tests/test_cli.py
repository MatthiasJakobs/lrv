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


class DummyCurses:
    def curs_set(self, visible):
        self.cursor_visible = visible


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

    def test_tui_insert_mode_saves_comment_on_selected_diff_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 2
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '+        return 1')

            app.start_input(1, DummyCurses())
            self.assertEqual(app.mode, 'input')
            app.input_text = 'Keep rejecting invalid quantities.'
            app.save_input(DummyCurses())

            state = load_state(repo)
            comment = state.comments[-1]
            self.assertEqual(comment.id, 'LPR-003')
            self.assertEqual(comment.state, 'open')
            self.assertEqual(comment.file, 'src/parser.py')
            self.assertEqual(comment.side, 'new')
            self.assertEqual(comment.line_range.start, 7)
            self.assertEqual(comment.body, 'Keep rejecting invalid quantities.')
            self.assertEqual(comment.placement, 'after')
            self.assertEqual(comment.hunk.header, '@@ -4,7 +4,7 @@ from __future__ import annotations')
            self.assertIn('+        return 1', comment.hunk.snapshot)

    def test_tui_o_and_O_insert_below_and_above_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 2
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '+        return 1')

            app.start_input(1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, '+        return 1')
            app.cancel_input(DummyCurses())
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '+        return 1')

            app.start_input(-1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, '+        return 1')
            self.assertEqual(app.input_placement, 'before')

    def test_tui_o_and_O_on_total_line_place_comment_below_and_above(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '     total = 0.0')

            app.start_input(1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, '     total = 0.0')
            self.assertEqual(app.input_placement, 'after')
            app.cancel_input(DummyCurses())
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '     total = 0.0')

            app.start_input(-1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, '     total = 0.0')
            self.assertEqual(app.input_placement, 'before')

    def test_tui_O_renders_saved_comment_above_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '     total = 0.0')

            app.start_input(-1, DummyCurses())
            app.input_text = 'Initialize this closer to use.'
            app.save_input(DummyCurses())

            lines = app.selected_file_lines()
            comment_index = next(index for index, line in enumerate(lines) if line == '>>> Initialize this closer to use.')
            target_index = next(index for index, line in enumerate(lines) if line == '     total = 0.0')
            self.assertLess(comment_index, target_index)

    def test_tui_o_renders_saved_comment_below_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text == '     total = 0.0')

            app.start_input(1, DummyCurses())
            app.input_text = 'This accumulator is visible.'
            app.save_input(DummyCurses())

            lines = app.selected_file_lines()
            comment_index = next(index for index, line in enumerate(lines) if line == '>>> This accumulator is visible.')
            target_index = next(index for index, line in enumerate(lines) if line == '     total = 0.0')
            self.assertGreater(comment_index, target_index)

    def test_tui_o_and_O_can_insert_at_bottom_and_top_of_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 2
            app.focus = 'diff'
            rows = app.selected_file_rows()

            app.diff_line = 0
            app.start_input(-1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, ' def parse_quantity(raw: str) -> int:')
            app.cancel_input(DummyCurses())

            app.diff_line = len(rows) - 1
            app.start_input(1, DummyCurses())
            self.assertEqual(rows[app.diff_line].text, '     return value')

    def test_tui_d_deletes_selected_inline_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 2
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text.startswith('>>> LPR-001 '))

            app.delete_selected_comment()

            state = load_state(repo)
            self.assertNotIn('LPR-001', [comment.id for comment in state.comments])
            self.assertIn('LPR-002', [comment.id for comment in state.comments])

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

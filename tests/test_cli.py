import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_fixture_repo import materialize
from lpr.state import load_state
from lpr.tui import RenderedLine, ReviewApp, minimap_buckets, minimap_viewport


ROOT = Path(__file__).resolve().parents[1]


class DummyCurses:
    KEY_ENTER = 10
    KEY_UP = 259
    KEY_DOWN = 258

    def curs_set(self, visible):
        self.cursor_visible = visible


class CliTest(unittest.TestCase):
    def run_lpr(self, repo, *args):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        return subprocess.run([sys.executable, '-m', 'lpr', *args], cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def add_parser_comment(self, repo, body='Keep rejecting invalid quantities.'):
        app = ReviewApp(repo, load_state(repo))
        self.select_file(app, 'src/parser.py')
        app.focus = 'diff'
        rows = app.selected_file_rows()
        app.diff_line = self.row_index_ending(rows, '        return 1')
        app.start_input(1, DummyCurses())
        app.input_text = body
        app.save_input(DummyCurses())
        return load_state(repo).comments[-1]

    def comments_by_id(self, repo):
        return {comment.id: comment for comment in load_state(repo).comments}

    def select_file(self, app, path):
        app.selected = next(index for index, file in enumerate(app.files) if file.path == path)

    def row_index_ending(self, rows, text):
        return next(index for index, row in enumerate(rows) if row.text.endswith(text))

    def line_index_ending(self, lines, text):
        return next(index for index, line in enumerate(lines) if line.endswith(text))

    def test_status_lists_changed_files_and_comment_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'status')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/long_review.py\n  M src/parser.py\n  M src/receipts.py\n  ?? src/reports.py\n  M src/taxes.py', result.stdout)
            self.assertIn('  open: 2 (LPR-001, LPR-003)', result.stdout)
            self.assertIn('  superseded: 1 (LPR-002)', result.stdout)
            self.assertIn('  dismissed: 1 (LPR-004)', result.stdout)

    def test_minimap_buckets_prioritize_comments_and_diffs(self):
        rows = [
            RenderedLine('    1 unchanged', None, kind='unchanged'),
            RenderedLine('+   2 added', None, kind='added'),
            RenderedLine('-   3 deleted', None, kind='deleted'),
            RenderedLine('>>> comment', None, 'LPR-001'),
        ]

        self.assertEqual(minimap_buckets(rows, 4), ['unchanged', 'added', 'deleted', 'comment'])
        self.assertEqual(minimap_buckets(rows[1:3], 1), ['mixed'])

    def test_minimap_viewport_maps_scroll_to_compressed_rows(self):
        self.assertEqual(minimap_viewport(100, 0, 20, 10), (0, 1))
        self.assertEqual(minimap_viewport(100, 50, 20, 10), (5, 6))
        self.assertEqual(minimap_viewport(100, 90, 20, 10), (9, 9))

    def test_bare_command_prints_review_when_not_interactive(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('lpr review - HEAD', result.stdout)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/long_review.py\n  M src/parser.py\n  M src/receipts.py\n  ?? src/reports.py\n  M src/taxes.py', result.stdout)
            self.assertIn('--- M src/parser.py ---', result.stdout)
            self.assertIn('  40 def format_total(total):', result.stdout)
            self.assertIn('-   4         raise ValueError(\'quantity must be positive\')', result.stdout)
            self.assertIn('+   4         return 1', result.stdout)
            self.assertIn('>>> LPR-001 [open] src/parser.py:4', result.stdout)
            self.assertIn('>>> This silently changes invalid input instead of rejecting it.', result.stdout)

    def test_bare_command_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--- ?? src/formatters.py ---', result.stdout)
            self.assertIn('+   1 def format_receipt_line(name, quantity, price):', result.stdout)

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

    def test_tui_ctrl_d_and_ctrl_u_half_page_in_diff_focus(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            app.focus = 'diff'
            app.visible_diff_height = 12
            app.half_page_diff(1)
            self.assertEqual(app.diff_line, 6)
            app.half_page_diff(-1)
            self.assertEqual(app.diff_line, 0)

    def test_tui_ctrl_d_and_ctrl_u_ignore_file_focus(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            app.focus = 'files'
            app.selected = 1
            app.diff_line = 3
            app.visible_diff_height = 12
            app.half_page_diff(1)
            self.assertEqual(app.focus, 'files')
            self.assertEqual(app.selected, 1)
            self.assertEqual(app.diff_line, 3)

    def test_tui_diff_progress_label_matches_selected_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            line_count = len(app.selected_file_lines())

            app.diff_line = 0
            self.assertEqual(app.diff_progress_label(), 'Top')
            app.diff_line = line_count // 2
            self.assertEqual(app.diff_progress_label(), f'{round((app.diff_line + 1) * 100 / line_count)}%')
            app.diff_line = line_count - 1
            self.assertEqual(app.diff_progress_label(), 'Bot')

    def test_tui_comment_modal_lists_open_and_superseded_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            app.open_comment_modal()

            self.assertEqual(app.mode, 'comments')
            self.assertEqual(app.modal_comment_ids, ('LPR-001', 'LPR-002', 'LPR-003'))

    def test_tui_comment_modal_state_changes_stay_visible_until_close(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.open_comment_modal()

            app.transition_selected_modal_comment('resolved')

            self.assertEqual(app.modal_comment_ids, ('LPR-001', 'LPR-002', 'LPR-003'))
            self.assertEqual(app.modal_comments()[0].state, 'resolved')
            self.assertEqual(load_state(repo).comments[0].state, 'resolved')

            app.close_comment_modal()
            app.open_comment_modal()

            self.assertEqual(app.modal_comment_ids, ('LPR-002', 'LPR-003'))

    def test_tui_comment_modal_state_keys_toggle_to_original_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.open_comment_modal()

            app.transition_selected_modal_comment('resolved')
            self.assertEqual(app.modal_comments()[0].state, 'resolved')

            app.transition_selected_modal_comment('resolved')
            self.assertEqual(app.modal_comments()[0].state, 'open')
            self.assertEqual(load_state(repo).comments[0].state, 'open')

            app.transition_selected_modal_comment('dismissed')
            self.assertEqual(app.modal_comments()[0].state, 'dismissed')

            app.transition_selected_modal_comment('dismissed')
            self.assertEqual(app.modal_comments()[0].state, 'open')

    def test_tui_comment_modal_jump_selects_comment_location(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.open_comment_modal()
            app.modal_index = 2

            app.jump_to_modal_comment()

            self.assertEqual(app.focus, 'diff')
            self.assertEqual(app.files[app.selected].path, 'src/receipts.py')
            self.assertEqual(app.selected_file_rows()[app.diff_line].comment_id, 'LPR-003')

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
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_input(1, DummyCurses())
            self.assertEqual(app.mode, 'input')
            app.input_text = 'Keep rejecting invalid quantities.'
            app.save_input(DummyCurses())

            state = load_state(repo)
            comment = state.comments[-1]
            self.assertEqual(comment.id, 'LPR-005')
            self.assertEqual(comment.state, 'open')
            self.assertEqual(comment.file, 'src/parser.py')
            self.assertEqual(comment.side, 'new')
            self.assertEqual(comment.line_range.start, 4)
            self.assertEqual(comment.body, 'Keep rejecting invalid quantities.')
            self.assertEqual(comment.placement, 'after')
            self.assertEqual(comment.hunk.header, '@@ -1,28 +1,31 @@')
            self.assertIn('+        return 1', comment.hunk.snapshot)

    def test_tui_insert_mode_keeps_cursor_position_after_saving_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = len(rows) - 1
            app.scroll = max(1, len(rows) - 6)
            app.start_input(1, DummyCurses())
            anchor = app.input_anchor
            scroll = app.scroll

            app.input_text = 'Keep this visible.'
            app.save_input(DummyCurses())

            selected = app.selected_file_rows()[app.diff_line]
            self.assertEqual(app.scroll, scroll)
            self.assertIsNotNone(selected.anchor)
            self.assertEqual(selected.anchor.file, anchor.file)
            self.assertEqual(selected.anchor.side, anchor.side)
            self.assertEqual(selected.anchor.line, anchor.line)

    def test_tui_insert_mode_marks_comment_superseded_when_hunk_changes_before_save(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')
            app.start_input(1, DummyCurses())
            parser = repo / 'src' / 'parser.py'
            parser.write_text(parser.read_text().replace("return sku or 'UNKNOWN'", "return sku or 'UNKNOWN-SKU'"))

            app.input_text = 'Keep rejecting invalid quantities.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            self.assertEqual(comment.state, 'superseded')

    def test_tui_o_and_O_insert_below_and_above_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_input(1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('        return 1'))
            app.cancel_input(DummyCurses())
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_input(-1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('        return 1'))
            self.assertEqual(app.input_placement, 'before')

    def test_tui_o_and_O_on_total_line_place_comment_below_and_above(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '    total = 0.0')

            app.start_input(1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('    total = 0.0'))
            self.assertEqual(app.input_placement, 'after')
            app.cancel_input(DummyCurses())
            app.diff_line = self.row_index_ending(rows, '    total = 0.0')

            app.start_input(-1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('    total = 0.0'))
            self.assertEqual(app.input_placement, 'before')

    def test_tui_O_renders_saved_comment_above_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '    total = 0.0')

            app.start_input(-1, DummyCurses())
            app.input_text = 'Initialize this closer to use.'
            app.save_input(DummyCurses())

            lines = app.selected_file_lines()
            comment_index = next(index for index, line in enumerate(lines) if line == '>>> Initialize this closer to use.')
            target_index = self.line_index_ending(lines, '    total = 0.0')
            self.assertLess(comment_index, target_index)

    def test_tui_o_renders_saved_comment_below_current_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.selected = 0
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '    total = 0.0')

            app.start_input(1, DummyCurses())
            app.input_text = 'This accumulator is visible.'
            app.save_input(DummyCurses())

            lines = app.selected_file_lines()
            comment_index = next(index for index, line in enumerate(lines) if line == '>>> This accumulator is visible.')
            target_index = self.line_index_ending(lines, '    total = 0.0')
            self.assertGreater(comment_index, target_index)

    def test_tui_o_and_O_can_insert_at_bottom_and_top_of_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()

            app.diff_line = 0
            app.start_input(-1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('def parse_quantity(raw):'))
            app.cancel_input(DummyCurses())

            app.diff_line = len(rows) - 1
            app.start_input(1, DummyCurses())
            self.assertTrue(rows[app.diff_line].text.endswith('    }'))

    def test_tui_d_deletes_selected_inline_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text.startswith('>>> LPR-001 '))

            app.delete_selected_comment()

            state = load_state(repo)
            self.assertNotIn('LPR-001', [comment.id for comment in state.comments])
            self.assertIn('LPR-002', [comment.id for comment in state.comments])

    def test_tui_refresh_marks_comment_superseded_when_commented_hunk_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            comment = self.add_parser_comment(repo)
            parser = repo / 'src' / 'parser.py'
            parser.write_text(parser.read_text().replace("return sku or 'UNKNOWN'", "return sku or 'UNKNOWN-SKU'"))

            ReviewApp(repo, load_state(repo)).reload()

            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'superseded')

    def test_tui_refresh_keeps_comment_open_when_unrelated_hunk_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            comment = self.add_parser_comment(repo)
            taxes = repo / 'src' / 'taxes.py'
            taxes.write_text(taxes.read_text().replace("'airport': 9.25", "'airport': 9.5"))

            ReviewApp(repo, load_state(repo)).reload()

            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'open')

    def test_tui_refresh_marks_comment_superseded_when_commented_hunk_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            comment = self.add_parser_comment(repo)
            subprocess.run(['git', 'checkout', '--', 'src/parser.py'], cwd=repo, check=True)

            ReviewApp(repo, load_state(repo)).reload()

            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'superseded')

    def test_export_prints_only_open_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('# LPR Review', result.stdout)
            self.assertIn('### LPR-001 src/parser.py:4', result.stdout)
            self.assertIn('This silently changes invalid input instead of rejecting it.', result.stdout)
            self.assertIn('### LPR-003 src/receipts.py:18', result.stdout)
            self.assertIn('Receipt notes need escaping or filtering before they are printed.', result.stdout)
            self.assertIn('```diff\n@@ -1,28 +1,31 @@', result.stdout)
            self.assertIn('Do not resolve, dismiss, or clear LPR comments.', result.stdout)
            self.assertNotIn('LPR-002', result.stdout)
            self.assertNotIn('LPR-004', result.stdout)

    def test_export_marks_changed_commented_hunk_superseded_before_printing(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            comment = self.add_parser_comment(repo)
            parser = repo / 'src' / 'parser.py'
            parser.write_text(parser.read_text().replace("return sku or 'UNKNOWN'", "return sku or 'UNKNOWN-SKU'"))

            result = self.run_lpr(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(comment.id, result.stdout)
            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'superseded')

    def test_export_accepts_repository_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, 'export', str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:4', result.stdout)

    def test_export_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:4', result.stdout)

    def test_export_accepts_repo_option(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lpr(ROOT, '--repo', str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LPR-001 src/parser.py:4', result.stdout)

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

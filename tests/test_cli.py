import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.materialize_fixture_repo import materialize
from lrv.state import Comment, FileAnchor, LineRange, ReviewState, load_state, save_state
import lrv.tui as tui
from lrv.tui import RenderedLine, ReviewApp, minimap_buckets, minimap_viewport, source_row_parts, syntax_token_kind


ROOT = Path(__file__).resolve().parents[1]


class DummyCurses:
    KEY_ENTER = 10
    KEY_UP = 259
    KEY_DOWN = 258
    KEY_PPAGE = 339
    KEY_NPAGE = 338
    KEY_LEFT = 260
    KEY_RIGHT = 261
    A_NORMAL = 0
    A_BOLD = 1
    A_REVERSE = 2
    A_DIM = 4
    A_UNDERLINE = 8
    COLOR_BLACK = 0
    COLOR_RED = 1
    COLOR_GREEN = 2
    COLOR_YELLOW = 3
    COLOR_BLUE = 4
    COLOR_MAGENTA = 5
    COLOR_CYAN = 6
    COLOR_WHITE = 7
    COLORS = 256
    color_changes = []
    pairs = []

    def has_colors(self):
        return True

    def start_color(self):
        self.started_color = True

    def use_default_colors(self):
        self.default_colors = True

    def init_pair(self, pair, foreground, background):
        self.pairs.append((pair, foreground, background))

    def curs_set(self, visible):
        self.cursor_visible = visible

    def color_pair(self, pair):
        return pair * 16

    def can_change_color(self):
        return True

    def init_color(self, color, red, green, blue):
        self.color_changes.append((color, red, green, blue))


class LowColorCurses(DummyCurses):
    COLORS = 8


class FixedPaletteCurses(DummyCurses):
    def can_change_color(self):
        return False


class FakeScreen:
    def __init__(self):
        self.calls = []

    def addstr(self, y, x, value, attr=0):
        self.calls.append((y, x, value, attr))


class CliTest(unittest.TestCase):
    def run_lrv(self, repo, *args):
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        return subprocess.run([sys.executable, '-m', 'lrv', *args], cwd=repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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

    def make_long_repo_with_early_change(self, repo):
        repo.mkdir()
        subprocess.run(['git', 'init'], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=repo, check=True)
        source = repo / 'long_file.py'
        source.write_text(''.join(f'LINE_{line} = {line}\n' for line in range(1, 301)))
        subprocess.run(['git', 'add', 'long_file.py'], cwd=repo, check=True)
        subprocess.run(['git', 'commit', '-m', 'base'], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = source.read_text().splitlines()
        lines[24] = 'LINE_25 = 2500'
        source.write_text('\n'.join(lines) + '\n')

    def test_status_lists_changed_files_and_comment_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(repo, 'status')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/long_review.py\n  M src/parser.py\n  M src/receipts.py\n  ?? src/reports.py\n  M src/taxes.py', result.stdout)
            self.assertIn('  open: 2 (LRV-001, LRV-003)', result.stdout)
            self.assertIn('  superseded: 1 (LRV-002)', result.stdout)
            self.assertIn('  dismissed: 1 (LRV-004)', result.stdout)

    def test_sidebar_rows_reflect_folder_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))

            rows = tui.sidebar_rows(app.files)

            self.assertEqual([(row.kind, row.depth, row.name) for row in rows[:3]], [('folder', 0, 'src'), ('file', 1, 'calculator.py'), ('file', 1, 'formatters.py')])

    def test_sidebar_file_stats_count_diff_rows_and_active_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            file = next(file for file in app.files if file.path == 'src/calculator.py')

            added, removed = tui.file_change_counts(repo, file)
            comments = tui.file_comment_count(app.state, file.path)

            self.assertEqual((added, removed, comments), (17, 10, 1))

    def test_draw_sidebar_file_colors_counts(self):
        app = ReviewApp.__new__(ReviewApp)
        screen = FakeScreen()
        curses = DummyCurses()
        row = tui.SidebarRow('file', 'src/parser.py', 'parser.py', 1, 0)
        file = tui.ChangedFile('src/parser.py', 'M')

        app.draw_sidebar_file(screen, 4, 1, 36, row, file, 33, 15, 2, DummyCurses.A_NORMAL, curses)

        self.assertIn((4, 20, '+', curses.color_pair(2) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 21, '  33', curses.color_pair(2) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 26, '-', curses.color_pair(3) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 27, '  15', curses.color_pair(3) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 32, '*', curses.color_pair(11) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 33, '   2', curses.color_pair(11) | DummyCurses.A_BOLD), screen.calls)

    def test_draw_sidebar_file_highlights_whole_row(self):
        app = ReviewApp.__new__(ReviewApp)
        screen = FakeScreen()
        curses = DummyCurses()
        row = tui.SidebarRow('file', 'src/parser.py', 'parser.py', 1, 0)
        file = tui.ChangedFile('src/parser.py', 'M')
        selected_attr = DummyCurses.A_DIM

        app.draw_sidebar_file(screen, 4, 1, 36, row, file, 33, 15, 2, selected_attr, curses)

        self.assertIn((4, 1, ' ' * 36, selected_attr), screen.calls)
        self.assertIn((4, 1, '  M ', selected_attr), screen.calls)
        self.assertIn((4, 5, 'parser.py', selected_attr), screen.calls)
        self.assertIn((4, 19, ' ', selected_attr), screen.calls)
        self.assertIn((4, 20, '+', curses.color_pair(2) | DummyCurses.A_BOLD | selected_attr), screen.calls)
        self.assertIn((4, 21, '  33', curses.color_pair(2) | DummyCurses.A_BOLD | selected_attr), screen.calls)
        self.assertIn((4, 25, ' ', selected_attr), screen.calls)
        self.assertIn((4, 26, '-', curses.color_pair(3) | DummyCurses.A_BOLD | selected_attr), screen.calls)
        self.assertIn((4, 27, '  15', curses.color_pair(3) | DummyCurses.A_BOLD | selected_attr), screen.calls)
        self.assertIn((4, 31, ' ', selected_attr), screen.calls)
        self.assertIn((4, 32, '*', curses.color_pair(11) | DummyCurses.A_BOLD | selected_attr), screen.calls)
        self.assertIn((4, 33, '   2', curses.color_pair(11) | DummyCurses.A_BOLD | selected_attr), screen.calls)

    def test_draw_sidebar_file_hides_zero_counts_but_keeps_columns(self):
        app = ReviewApp.__new__(ReviewApp)
        screen = FakeScreen()
        curses = DummyCurses()
        row = tui.SidebarRow('file', 'src/parser.py', 'parser.py', 1, 0)
        file = tui.ChangedFile('src/parser.py', 'M')

        app.draw_sidebar_file(screen, 4, 1, 36, row, file, 0, 15, 0, DummyCurses.A_NORMAL, curses)

        self.assertIn((4, 20, ' ', curses.color_pair(2) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 21, '    ', curses.color_pair(2) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 26, '-', curses.color_pair(3) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 27, '  15', curses.color_pair(3) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 32, ' ', curses.color_pair(11) | DummyCurses.A_BOLD), screen.calls)
        self.assertIn((4, 33, '    ', curses.color_pair(11) | DummyCurses.A_BOLD), screen.calls)

    def test_minimap_buckets_prioritize_comments_and_diffs(self):
        rows = [
            RenderedLine('    1 unchanged', None, kind='unchanged'),
            RenderedLine('+   2 added', None, kind='added'),
            RenderedLine('-   3 deleted', None, kind='deleted'),
            RenderedLine('>>> comment', None, 'LRV-001'),
            RenderedLine('    4 visual', None, kind='visual'),
        ]

        self.assertEqual(minimap_buckets(rows, 5), ['unchanged', 'added', 'deleted', 'comment', 'visual'])
        self.assertEqual(minimap_buckets(rows[1:3], 1), ['mixed'])

    def test_minimap_buckets_treat_target_lines_as_comments(self):
        rows = [
            RenderedLine('    1 unchanged', None, kind='unchanged'),
            RenderedLine('    2 target', None, kind='unchanged', target_comment_state='open'),
        ]

        self.assertEqual(minimap_buckets(rows, 2), ['unchanged', 'comment'])

    def test_minimap_viewport_maps_scroll_to_compressed_rows(self):
        self.assertEqual(minimap_viewport(100, 0, 20, 10), (0, 1))
        self.assertEqual(minimap_viewport(100, 50, 20, 10), (5, 6))
        self.assertEqual(minimap_viewport(100, 90, 20, 10), (9, 9))

    def test_bare_command_prints_review_when_not_interactive(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(repo)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('lrv review - HEAD', result.stdout)
            self.assertIn('Changed files:\n  M src/calculator.py\n  ?? src/formatters.py\n  M src/long_review.py\n  M src/parser.py\n  M src/receipts.py\n  ?? src/reports.py\n  M src/taxes.py', result.stdout)
            self.assertIn('--- M src/parser.py ---', result.stdout)
            self.assertIn('  40 def format_total(total):', result.stdout)
            self.assertIn('-   4         raise ValueError(\'quantity must be positive\')', result.stdout)
            self.assertIn('+   4         return 1', result.stdout)
            self.assertIn('>>> LRV-001 [open] src/parser.py:4', result.stdout)
            self.assertIn('>>> This silently changes invalid input instead of rejecting it.', result.stdout)

    def test_bare_command_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(ROOT, str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--- ?? src/formatters.py ---', result.stdout)
            self.assertIn('+   1 def format_receipt_line(name, quantity, price):', result.stdout)

    def test_source_row_parts_extracts_code_rows(self):
        self.assertEqual(source_row_parts(RenderedLine('   40 def format_total(total):', None, kind='unchanged')), ('   40 ', 'def format_total(total):'))
        self.assertEqual(source_row_parts(RenderedLine('+   4         return 1', None, kind='added')), ('+   4 ', '        return 1'))
        self.assertEqual(source_row_parts(RenderedLine('-   4         raise ValueError()', None, kind='deleted')), ('-   4 ', '        raise ValueError()'))
        self.assertIsNone(source_row_parts(RenderedLine('>>> LRV-001 [open]', None, 'LRV-001')))
        self.assertIsNone(source_row_parts(RenderedLine('   40 def format_total(total):', None, kind='visual')))

    def test_draw_source_row_draws_syntax_spans_for_unchanged_rows(self):
        app = ReviewApp.__new__(ReviewApp)
        app.syntax_colors = True
        screen = FakeScreen()
        original = tui.syntax_spans
        tui.syntax_spans = lambda path, code: [('def', 'keyword'), (' ', None), ('format_total', 'function')]
        try:
            row = RenderedLine('   40 def format_total', None, kind='unchanged')

            rendered = app.draw_source_row(screen, 3, 10, row, 'src/calculator.py', 80, DummyCurses.A_NORMAL, False, DummyCurses())
        finally:
            tui.syntax_spans = original

        self.assertTrue(rendered)
        self.assertEqual(screen.calls[0], (3, 10, '   40 ', DummyCurses.A_NORMAL))
        self.assertEqual(screen.calls[1], (3, 16, 'def', 9 * 16 | DummyCurses.A_BOLD))
        self.assertEqual(screen.calls[2], (3, 19, ' ', DummyCurses.A_NORMAL))
        self.assertEqual(screen.calls[3], (3, 20, 'format_total', 12 * 16 | DummyCurses.A_BOLD))

    def test_current_line_attr_is_subtle(self):
        app = ReviewApp.__new__(ReviewApp)
        app.current_line_background = True

        self.assertEqual(app.line_attr(DummyCurses(), RenderedLine('   40 code', None, kind='unchanged')), DummyCurses.A_NORMAL)
        self.assertEqual(app.current_line_attr(DummyCurses()), DummyCurses().color_pair(tui.CURRENT_LINE_BG_PAIR))

    def test_current_line_attr_falls_back_to_dim_without_background(self):
        app = ReviewApp.__new__(ReviewApp)
        app.current_line_background = False

        self.assertEqual(app.current_line_attr(DummyCurses()), DummyCurses.A_DIM)

    def test_current_line_background_color_uses_muted_custom_color_when_supported(self):
        app = ReviewApp.__new__(ReviewApp)
        curses = DummyCurses()
        curses.color_changes = []

        color = app.current_line_background_color(curses)

        self.assertEqual(color, 104)
        self.assertEqual(curses.color_changes, [(104, 35, 35, 35)])

    def test_current_line_background_color_falls_back_without_custom_palette(self):
        app = ReviewApp.__new__(ReviewApp)

        self.assertEqual(app.current_line_background_color(LowColorCurses()), 236)

    def test_current_line_background_color_uses_nearest_fixed_palette_color(self):
        app = ReviewApp.__new__(ReviewApp)
        app.theme = tui.default_theme()
        app.theme['current_line_bg'] = '#152b43'

        self.assertEqual(app.current_line_background_color(FixedPaletteCurses()), 236)

    def test_inline_comment_rows_keep_comment_state(self):
        comment = Comment(
            id='LRV-101',
            state='superseded',
            file='src/parser.py',
            side='new',
            line_range=LineRange(4, 4),
            hunk=None,
            body='Recheck this.',
            created_at='2026-07-04T10:00:00Z',
            updated_at='2026-07-04T10:00:00Z',
        )

        rows = tui.inline_comment_rows(comment)

        self.assertEqual([row.comment_state for row in rows], ['superseded', 'superseded', 'superseded', 'superseded'])
        self.assertEqual([row.kind for row in rows], ['comment_top', 'comment_title', 'comment_body', 'comment_bottom'])

    def test_inline_comment_attr_uses_modal_state_colors(self):
        app = ReviewApp.__new__(ReviewApp)
        curses = DummyCurses()

        states = {
            'open': curses.color_pair(5) | curses.A_BOLD,
            'superseded': curses.color_pair(tui.COMMENT_STATE_FG_PAIRS['superseded']),
            'resolved': curses.color_pair(tui.COMMENT_STATE_FG_PAIRS['resolved']),
            'dismissed': curses.color_pair(tui.COMMENT_STATE_FG_PAIRS['dismissed']),
        }
        for state, attr in states.items():
            row = RenderedLine('comment', None, 'LRV-001', kind='comment_body', comment_state=state)

            self.assertEqual(app.line_attr(curses, row), attr)

    def test_plain_text_for_comment_rows_preserves_cli_comment_format(self):
        rows = [
            RenderedLine('', None, 'LRV-001', kind='comment_top', comment_state='open'),
            RenderedLine('>>> LRV-001 [open] src/parser.py:4', None, 'LRV-001', kind='comment_title', comment_state='open'),
            RenderedLine('>>> Check this.', None, 'LRV-001', kind='comment_body', comment_state='open'),
            RenderedLine('', None, 'LRV-001', kind='comment_bottom', comment_state='open'),
        ]

        self.assertEqual([tui.plain_text_for_row(row) for row in rows], [None, '>>> LRV-001 [open] src/parser.py:4', '>>> Check this.', None])

    def test_draw_comment_box_row_uses_full_width_unicode_border(self):
        app = ReviewApp.__new__(ReviewApp)
        screen = FakeScreen()
        curses = DummyCurses()

        app.draw_comment_box_row(screen, 2, 4, RenderedLine('', None, 'LRV-001', kind='comment_top', comment_state='open'), 12, curses.color_pair(5), False, curses)
        app.draw_comment_box_row(screen, 3, 4, RenderedLine('>>> LRV-001 [open] src/parser.py:4', None, 'LRV-001', kind='comment_title', comment_state='open'), 12, curses.color_pair(5), False, curses)
        app.draw_comment_box_row(screen, 4, 4, RenderedLine('', None, 'LRV-001', kind='comment_bottom', comment_state='open'), 12, curses.color_pair(5), False, curses)

        self.assertEqual(screen.calls[0], (2, 4, '┌──────────┐', curses.color_pair(5)))
        self.assertEqual(screen.calls[1], (3, 4, '│ Note - LR│', curses.color_pair(5)))
        self.assertEqual(screen.calls[2], (4, 4, '└──────────┘', curses.color_pair(5)))

    def test_ui_glyphs_can_fallback_to_ascii(self):
        original = os.environ.get('LRV_ASCII')
        os.environ['LRV_ASCII'] = '1'
        try:
            self.assertEqual(tui.ui_glyphs()['vertical'], '|')
            self.assertEqual(tui.ui_glyphs()['horizontal'], '-')
        finally:
            if original is None:
                os.environ.pop('LRV_ASCII', None)
            else:
                os.environ['LRV_ASCII'] = original

    def test_status_bar_text_is_pure_review_status(self):
        state = ReviewState(version=1, repo_root='', base_commit='abc123', comments=())
        files = [tui.ChangedFile('src/parser.py', 'M'), tui.ChangedFile('src/taxes.py', 'M')]
        change_counts = {
            'src/parser.py': (4, 1),
            'src/taxes.py': (2, 3),
        }

        self.assertEqual(tui.status_bar_text(ROOT, state, files, change_counts), 'lrv review  HEAD abc123  2 files  +6  -4  0 notes')

    def test_centered_text_pads_status_evenly(self):
        app = ReviewApp.__new__(ReviewApp)

        self.assertEqual(app.centered_text('status', 10), '  status  ')
        self.assertEqual(app.centered_text('long status', 4), 'long')

    def test_comment_target_attr_uses_background_colors_when_supported(self):
        app = ReviewApp.__new__(ReviewApp)
        app.changed_line_backgrounds = True
        curses = DummyCurses()

        self.assertEqual(app.line_attr(curses, RenderedLine('    4 code', None, kind='unchanged', target_comment_state='open')), curses.color_pair(38))
        self.assertEqual(app.line_attr(curses, RenderedLine('    4 code', None, kind='unchanged', target_comment_state='superseded')), curses.color_pair(39))

    def test_comment_target_attr_falls_back_to_state_colors_without_backgrounds(self):
        app = ReviewApp.__new__(ReviewApp)
        app.changed_line_backgrounds = False
        curses = DummyCurses()

        self.assertEqual(app.line_attr(curses, RenderedLine('    4 code', None, kind='unchanged', target_comment_state='open')), curses.color_pair(5))
        self.assertEqual(app.line_attr(curses, RenderedLine('    4 code', None, kind='unchanged', target_comment_state='superseded')), curses.color_pair(tui.COMMENT_STATE_FG_PAIRS['superseded']))

    def test_comment_target_attr_overrides_changed_line_backgrounds(self):
        app = ReviewApp.__new__(ReviewApp)
        app.changed_line_backgrounds = True
        curses = DummyCurses()

        row = RenderedLine('+   4 code', None, kind='added', target_comment_state='open')

        self.assertEqual(app.line_attr(curses, row), curses.color_pair(38))

    def test_changed_line_backgrounds_require_extended_colors(self):
        app = ReviewApp.__new__(ReviewApp)

        self.assertFalse(app.changed_line_backgrounds_supported(LowColorCurses()))
        self.assertTrue(app.changed_line_backgrounds_supported(DummyCurses()))

    def test_changed_background_color_uses_muted_custom_color_when_supported(self):
        app = ReviewApp.__new__(ReviewApp)
        curses = DummyCurses()
        curses.color_changes = []

        color = app.changed_background_color(curses, 'added')

        self.assertEqual(color, 100)
        self.assertEqual(curses.color_changes, [(100, 20, 55, 35)])

    def test_changed_background_color_falls_back_without_custom_palette(self):
        app = ReviewApp.__new__(ReviewApp)

        self.assertEqual(app.changed_background_color(LowColorCurses(), 'added'), 22)

    def test_changed_background_color_uses_nearest_fixed_palette_color(self):
        app = ReviewApp.__new__(ReviewApp)
        app.theme = tui.default_theme()
        app.theme['diff_added_bg'] = '#123f2a'

        self.assertEqual(app.changed_background_color(FixedPaletteCurses(), 'added'), 22)

    def test_changed_color_pairs_keep_backgrounds_with_syntax_foregrounds(self):
        app = ReviewApp.__new__(ReviewApp)
        app.theme = tui.default_theme()
        curses = DummyCurses()
        curses.pairs = []
        curses.color_changes = []

        app.init_changed_color_pairs(curses)

        self.assertIn((tui.CHANGED_BG_PAIRS['added'], curses.COLOR_WHITE, 100), curses.pairs)
        self.assertIn((tui.CHANGED_BG_PAIRS['deleted'], curses.COLOR_WHITE, 101), curses.pairs)
        self.assertIn((tui.CHANGED_SYNTAX_PAIRS['added']['string'], tui.CUSTOM_COLOR_IDS['syntax_string_fg'], 100), curses.pairs)
        self.assertIn((tui.CHANGED_SYNTAX_PAIRS['deleted']['string'], tui.CUSTOM_COLOR_IDS['syntax_string_fg'], 101), curses.pairs)

    def test_comment_target_background_color_uses_muted_custom_color_when_supported(self):
        app = ReviewApp.__new__(ReviewApp)
        curses = DummyCurses()
        curses.color_changes = []

        color = app.comment_target_background_color(curses, 'open')

        self.assertEqual(color, 102)
        self.assertEqual(curses.color_changes, [(102, 71, 63, 16)])

    def test_comment_target_background_color_falls_back_without_custom_palette(self):
        app = ReviewApp.__new__(ReviewApp)

        self.assertEqual(app.comment_target_background_color(LowColorCurses(), 'superseded'), 24)

    def test_comment_target_background_color_uses_nearest_fixed_palette_color(self):
        app = ReviewApp.__new__(ReviewApp)
        app.theme = tui.default_theme()
        app.theme['comment_open_bg'] = '#101f35'

        self.assertEqual(app.comment_target_background_color(FixedPaletteCurses(), 'open'), 17)

    def test_syntax_token_kind_maps_common_pygments_tokens(self):
        if tui.Name is None:
            self.skipTest('pygments is not installed')

        self.assertEqual(syntax_token_kind(tui.Name.Builtin), 'builtin')
        self.assertEqual(syntax_token_kind(tui.Name.Attribute), 'attribute')
        self.assertEqual(syntax_token_kind(tui.Name.Decorator), 'decorator')
        self.assertEqual(syntax_token_kind(tui.Operator), 'operator')
        self.assertEqual(syntax_token_kind(tui.Punctuation), 'punctuation')

    def test_draw_source_row_draws_changed_rows_with_full_width_background(self):
        app = ReviewApp.__new__(ReviewApp)
        app.syntax_colors = True
        screen = FakeScreen()
        original = tui.syntax_spans
        tui.syntax_spans = lambda path, code: [('return', 'keyword'), (' 1\n', 'number')]
        try:
            row = RenderedLine('+   4 return 1', None, kind='added')

            rendered = app.draw_source_row(screen, 0, 0, row, 'src/parser.py', 20, DummyCurses().color_pair(14), False, DummyCurses())
        finally:
            tui.syntax_spans = original

        self.assertTrue(rendered)
        self.assertEqual(screen.calls[0], (0, 0, '+   4 ', 14 * 16))
        self.assertEqual(screen.calls[1], (0, 6, 'return', 19 * 16 | DummyCurses.A_BOLD))
        self.assertEqual(screen.calls[2], (0, 12, ' 1', 21 * 16))
        self.assertEqual(screen.calls[3], (0, 14, ' ' * 6, 14 * 16))

    def test_draw_source_row_uses_comment_target_color_for_whole_row(self):
        app = ReviewApp.__new__(ReviewApp)
        app.syntax_colors = True
        screen = FakeScreen()
        original = tui.syntax_spans
        tui.syntax_spans = lambda path, code: [('return', 'keyword')]
        try:
            row = RenderedLine('+   4 return 1', None, kind='added', target_comment_state='open')

            rendered = app.draw_source_row(screen, 0, 0, row, 'src/parser.py', 20, DummyCurses().color_pair(38), True, DummyCurses())
        finally:
            tui.syntax_spans = original

        self.assertTrue(rendered)
        self.assertEqual(screen.calls, [(0, 0, '+   4 return 1      ', 38 * 16 | DummyCurses.A_DIM)])

    def test_draw_source_row_skips_comment_rows(self):
        app = ReviewApp.__new__(ReviewApp)
        screen = FakeScreen()

        self.assertFalse(app.draw_source_row(screen, 0, 0, RenderedLine('>>> comment', None, 'LRV-001'), 'src/parser.py', 80, 0, False, DummyCurses()))
        self.assertEqual(screen.calls, [])

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

    def test_tui_gg_and_G_jump_to_first_and_last_diff_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            curses = DummyCurses()

            app.focus = 'diff'
            app.handle_normal_key(ord('G'), curses)
            self.assertEqual(app.diff_line, len(app.selected_file_lines()) - 1)

            app.handle_normal_key(ord('g'), curses)
            self.assertEqual(app.diff_line, len(app.selected_file_lines()) - 1)
            app.handle_normal_key(ord('g'), curses)
            self.assertEqual(app.diff_line, 0)

    def test_tui_gg_and_G_ignore_file_focus(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            curses = DummyCurses()

            app.focus = 'files'
            app.selected = 1
            app.diff_line = 3
            app.handle_normal_key(ord('G'), curses)
            app.handle_normal_key(ord('g'), curses)
            app.handle_normal_key(ord('g'), curses)
            self.assertEqual(app.focus, 'files')
            self.assertEqual(app.selected, 1)
            self.assertEqual(app.diff_line, 3)

    def test_tui_diff_progress_label_matches_selected_line(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            line_count = len(app.selected_file_rows())

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
            self.assertEqual(app.modal_comment_ids, ('LRV-001', 'LRV-002', 'LRV-003'))

    def test_tui_comment_modal_state_changes_stay_visible_until_close(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            app.open_comment_modal()

            app.transition_selected_modal_comment('resolved')

            self.assertEqual(app.modal_comment_ids, ('LRV-001', 'LRV-002', 'LRV-003'))
            self.assertEqual(app.modal_comments()[0].state, 'resolved')
            self.assertEqual(load_state(repo).comments[0].state, 'resolved')

            app.close_comment_modal()
            app.open_comment_modal()

            self.assertEqual(app.modal_comment_ids, ('LRV-002', 'LRV-003'))

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
            self.assertEqual(app.selected_file_rows()[app.diff_line].comment_id, 'LRV-003')

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
            self.assertEqual(comment.id, 'LRV-005')
            self.assertEqual(comment.state, 'open')
            self.assertEqual(comment.file, 'src/parser.py')
            self.assertEqual(comment.side, 'new')
            self.assertEqual(comment.line_range.start, 4)
            self.assertEqual(comment.body, 'Keep rejecting invalid quantities.')
            self.assertEqual(comment.placement, 'after')
            self.assertEqual(comment.hunk.header, '@@ -1,28 +1,31 @@')
            self.assertIn('+        return 1', comment.hunk.snapshot)

    def test_tui_i_edits_selected_inline_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.comment_id == 'LRV-001')

            original = next(comment for comment in app.state.comments if comment.id == 'LRV-001')
            app.start_comment_edit(DummyCurses())
            self.assertEqual(app.mode, 'input')
            self.assertEqual(app.input_text, original.body)
            app.input_text = 'Still reject invalid quantities.'
            app.save_input(DummyCurses())

            comment = next(comment for comment in load_state(repo).comments if comment.id == 'LRV-001')
            self.assertEqual(comment.body, 'Still reject invalid quantities.')
            self.assertEqual(comment.line_range.start, original.line_range.start)
            self.assertEqual(comment.line_range.end, original.line_range.end)
            self.assertEqual(comment.hunk.hash, original.hunk.hash)
            self.assertEqual(comment.created_at, original.created_at)
            self.assertNotEqual(comment.updated_at, original.updated_at)

    def test_tui_i_does_nothing_when_cursor_is_not_on_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_comment_edit(DummyCurses())

            self.assertEqual(app.mode, 'normal')
            self.assertIsNone(app.input_comment_id)

    def test_tui_visual_mode_comments_selected_line_range(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_visual()
            app.move_down()
            app.start_visual_input(DummyCurses())
            self.assertEqual(app.mode, 'input')
            app.input_text = 'Keep these cases explicit.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            self.assertEqual(comment.id, 'LRV-005')
            self.assertEqual(comment.file, 'src/parser.py')
            self.assertEqual(comment.side, 'new')
            self.assertEqual(comment.line_range.start, 4)
            self.assertEqual(comment.line_range.end, 5)
            self.assertEqual(comment.body, 'Keep these cases explicit.')

    def test_tui_visual_mode_marks_selected_rows_for_display(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_visual()
            app.move_down()

            rendered = app.rows_with_visual_selection(rows)
            visual_indexes = [index for index, row in enumerate(rendered) if row.kind == 'visual']
            self.assertEqual(visual_indexes, [app.visual_anchor, app.diff_line])

    def test_tui_visual_mode_overrides_comment_target_color(self):
        app = ReviewApp.__new__(ReviewApp)
        app.mode = 'visual'
        app.visual_anchor = 0
        app.diff_line = 0
        rows = [
            RenderedLine('    4 code', tui.DiffAnchor('src/parser.py', 'new', 4, None), kind='unchanged', target_comment_state='open'),
        ]
        app.selected_file_rows = lambda: rows

        rendered = app.rows_with_visual_selection(rows)

        self.assertEqual(rendered[0].kind, 'visual')
        self.assertEqual(app.line_attr(DummyCurses(), rendered[0]), DummyCurses().color_pair(8) | DummyCurses.A_BOLD)

    def test_rows_with_comment_targets_splits_overlapping_ranges_by_priority(self):
        rows = [
            RenderedLine(f'    {line} code', tui.DiffAnchor('src/parser.py', 'new', line, None), kind='unchanged')
            for line in range(10, 16)
        ]
        comments = [
            Comment(
                id='LRV-101',
                state='superseded',
                file='src/parser.py',
                side='new',
                line_range=LineRange(10, 15),
                hunk=None,
                body='Outer.',
                created_at='2026-07-04T10:00:00Z',
                updated_at='2026-07-04T10:00:00Z',
            ),
            Comment(
                id='LRV-102',
                state='open',
                file='src/parser.py',
                side='new',
                line_range=LineRange(12, 13),
                hunk=None,
                body='Inner.',
                created_at='2026-07-04T10:00:00Z',
                updated_at='2026-07-04T10:00:00Z',
            ),
            Comment(
                id='LRV-103',
                state='dismissed',
                file='src/parser.py',
                side='new',
                line_range=LineRange(14, 14),
                hunk=None,
                body='Hidden.',
                created_at='2026-07-04T10:00:00Z',
                updated_at='2026-07-04T10:00:00Z',
            ),
        ]

        rendered = tui.rows_with_comment_targets(rows, comments)

        self.assertEqual([row.target_comment_state for row in rendered], ['superseded', 'superseded', 'open', 'open', 'superseded', 'superseded'])

    def test_tui_visual_mode_starts_on_unchanged_line_outside_hunk(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')

            app.start_visual()

            self.assertEqual(app.mode, 'visual')
            self.assertEqual(app.diff_line, self.row_index_ending(rows, 'LINE_250 = 250'))
            self.assertEqual(app.status_message, '')

    def test_tui_insert_mode_saves_file_anchor_comment_outside_hunk(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')

            app.start_input(1, DummyCurses())
            app.input_text = 'Check this unchanged constant too.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            self.assertEqual(comment.anchor_kind, 'file')
            self.assertEqual(comment.file, 'long_file.py')
            self.assertEqual(comment.side, 'new')
            self.assertEqual(comment.line_range.start, 250)
            self.assertEqual(comment.line_range.end, 250)
            self.assertEqual(comment.file_anchor.snapshot, 'LINE_250 = 250')
            self.assertEqual(comment.file_anchor.prefix, ('LINE_247 = 247', 'LINE_248 = 248', 'LINE_249 = 249'))
            self.assertEqual(comment.file_anchor.suffix, ('LINE_251 = 251', 'LINE_252 = 252', 'LINE_253 = 253'))

    def test_tui_visual_mode_saves_file_anchor_range_outside_hunk(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')

            app.start_visual()
            app.move_down()
            app.move_down()
            app.start_visual_input(DummyCurses())
            app.input_text = 'These unchanged constants move together.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            self.assertEqual(comment.anchor_kind, 'file')
            self.assertEqual(comment.line_range.start, 250)
            self.assertEqual(comment.line_range.end, 252)
            self.assertEqual(comment.file_anchor.snapshot, 'LINE_250 = 250\nLINE_251 = 251\nLINE_252 = 252')

    def test_multiline_comments_render_after_final_target_line(self):
        rows = [
            RenderedLine(f'    {line} code', tui.DiffAnchor('src/parser.py', 'new', line, None), kind='unchanged')
            for line in range(10, 13)
        ]
        comment = Comment(
            id='LRV-101',
            state='open',
            file='src/parser.py',
            side='new',
            line_range=LineRange(10, 12),
            hunk=None,
            body='Range note.',
            created_at='2026-07-04T10:00:00Z',
            updated_at='2026-07-04T10:00:00Z',
            placement='before',
        )

        rendered = tui.rows_with_comments(rows, [comment])

        self.assertEqual([tui.plain_text_for_row(row) for row in rendered if tui.plain_text_for_row(row) is not None], [
            '    10 code',
            '    11 code',
            '    12 code',
            '>>> LRV-101 [open] src/parser.py:10-12',
            '>>> Range note.',
        ])

    def test_tui_visual_mode_saves_multiline_comment_after_range(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        return 1')

            app.start_visual()
            app.move_down()
            app.start_visual_input(DummyCurses())
            app.input_placement = 'before'
            app.input_text = 'Keep the range together.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            lines = app.selected_file_lines()
            comment_index = next(index for index, line in enumerate(lines) if line == '>>> Keep the range together.')
            target_index = self.line_index_ending(lines, '    return value')
            self.assertEqual(comment.placement, 'after')
            self.assertGreater(comment_index, target_index)

    def test_tui_visual_mode_rejects_mixed_hunk_and_file_anchor_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_25 = 2500')

            app.start_visual()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_visual_input(DummyCurses())

            self.assertEqual(app.mode, 'visual')
            self.assertEqual(app.status_message, 'Range comments cannot mix hunk and file lines.')

    def test_tui_visual_mode_rejects_mixed_old_and_new_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, '        raise ValueError(\'quantity must be positive\')')

            app.start_visual()
            app.move_down()
            app.start_visual_input(DummyCurses())

            self.assertEqual(app.mode, 'visual')
            self.assertEqual(app.status_message, 'Range comments cannot mix old and new lines.')

    def test_tui_visual_mode_rejects_cross_hunk_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/receipts.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.anchor is not None and row.anchor.line == 6)

            app.start_visual()
            app.diff_line = self.row_index_ending(rows, '        if quantity == 0:')
            app.start_visual_input(DummyCurses())

            self.assertEqual(app.mode, 'visual')
            self.assertEqual(app.status_message, 'Range comments cannot mix hunk and file lines.')

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
            app.diff_line = next(index for index, row in enumerate(rows) if row.text.startswith('>>> LRV-001 '))

            app.delete_selected_comment()

            state = load_state(repo)
            self.assertNotIn('LRV-001', [comment.id for comment in state.comments])
            self.assertIn('LRV-002', [comment.id for comment in state.comments])

    def test_tui_r_resolves_selected_inline_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text.startswith('>>> LRV-001 '))

            app.handle_normal_key(ord('r'), DummyCurses())

            self.assertEqual(self.comments_by_id(repo)['LRV-001'].state, 'resolved')

    def test_tui_shift_r_reloads(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            save_state(repo, ReviewState(version=1, repo_root=str(repo), base_commit='', comments=()))

            app.handle_normal_key(ord('R'), DummyCurses())

            self.assertEqual(app.state.comments, ())

    def test_tui_x_dismisses_selected_inline_comment(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/parser.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = next(index for index, row in enumerate(rows) if row.text.startswith('>>> LRV-001 '))

            app.handle_normal_key(ord('x'), DummyCurses())

            self.assertEqual(self.comments_by_id(repo)['LRV-001'].state, 'dismissed')

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

    def test_tui_refresh_keeps_file_anchor_comment_open_when_original_range_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_input(1, DummyCurses())
            app.input_text = 'Keep tracking this line.'
            app.save_input(DummyCurses())
            comment = load_state(repo).comments[-1]
            source = repo / 'long_file.py'
            source.write_text(source.read_text().replace('LINE_260 = 260', 'LINE_260 = 2600'))

            ReviewApp(repo, load_state(repo)).reload()

            refreshed = self.comments_by_id(repo)[comment.id]
            self.assertEqual(refreshed.state, 'open')
            self.assertEqual(refreshed.line_range.start, 250)

    def test_tui_refresh_relocates_file_anchor_comment_when_snapshot_moves_uniquely(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_input(1, DummyCurses())
            app.input_text = 'Keep tracking this line.'
            app.save_input(DummyCurses())
            comment = load_state(repo).comments[-1]
            source = repo / 'long_file.py'
            source.write_text('LINE_0 = 0\n' + source.read_text())

            ReviewApp(repo, load_state(repo)).reload()

            refreshed = self.comments_by_id(repo)[comment.id]
            self.assertEqual(refreshed.state, 'open')
            self.assertEqual(refreshed.line_range.start, 251)
            self.assertEqual(refreshed.line_range.end, 251)

    def test_tui_refresh_supersedes_file_anchor_comment_when_snapshot_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_input(1, DummyCurses())
            app.input_text = 'Keep tracking this line.'
            app.save_input(DummyCurses())
            comment = load_state(repo).comments[-1]
            source = repo / 'long_file.py'
            lines = [line for line in source.read_text().splitlines() if line != 'LINE_250 = 250']
            source.write_text('\n'.join(lines) + '\n')

            ReviewApp(repo, load_state(repo)).reload()

            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'superseded')

    def test_tui_refresh_supersedes_file_anchor_comment_when_relocation_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            state = load_state(repo)
            comment = Comment(
                id='LRV-001',
                state='open',
                file='long_file.py',
                side='new',
                line_range=LineRange(250, 250),
                hunk=None,
                body='Ambiguous on purpose.',
                created_at='2026-07-04T10:00:00Z',
                updated_at='2026-07-04T10:00:00Z',
                anchor_kind='file',
                file_anchor=FileAnchor('sha256:1cb4bbb41155d60663a77d18a344b0d0dfc43a9c90736e223bfe44f83d960b91', 'LINE_DUP = 1', [], []),
            )
            save_state(repo, ReviewState(state.version, state.repo_root, state.base_commit, (comment,)))
            source = repo / 'long_file.py'
            lines = source.read_text().splitlines()
            lines[249] = 'LINE_250_REMOVED = 250'
            lines.insert(100, 'LINE_DUP = 1')
            lines.insert(200, 'LINE_DUP = 1')
            source.write_text('\n'.join(lines) + '\n')

            ReviewApp(repo, load_state(repo)).reload()

            self.assertEqual(self.comments_by_id(repo)['LRV-001'].state, 'superseded')

    def test_tui_shows_comment_only_files_with_c_status(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_input(1, DummyCurses())
            app.input_text = 'Carry this review comment after diff disappears.'
            app.save_input(DummyCurses())
            subprocess.run(['git', 'checkout', '--', 'long_file.py'], cwd=repo, check=True)

            app = ReviewApp(repo, load_state(repo))

            self.assertEqual([(file.status, file.path) for file in app.files], [('C', 'long_file.py')])
            self.assertIn('>>> LRV-001 [open] long_file.py:250', app.selected_file_lines())

    def test_tui_untracked_files_continue_to_use_hunk_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            app = ReviewApp(repo, load_state(repo))
            self.select_file(app, 'src/reports.py')
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'def daily_sales_report(orders):')

            app.start_input(1, DummyCurses())
            app.input_text = 'This new helper is still part of the diff.'
            app.save_input(DummyCurses())

            comment = load_state(repo).comments[-1]
            self.assertEqual(comment.anchor_kind, 'hunk')
            self.assertIsNotNone(comment.hunk)
            self.assertIn('+def daily_sales_report(orders):', comment.hunk.snapshot)

    def test_export_prints_only_open_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('# LRV Review', result.stdout)
            self.assertIn('### LRV-001 src/parser.py:4', result.stdout)
            self.assertIn('This silently changes invalid input instead of rejecting it.', result.stdout)
            self.assertIn('### LRV-003 src/receipts.py:18', result.stdout)
            self.assertIn('Receipt notes need escaping or filtering before they are printed.', result.stdout)
            self.assertIn('```diff\n@@ -1,28 +1,31 @@', result.stdout)
            self.assertIn('Do not resolve, dismiss, or clear LRV comments.', result.stdout)
            self.assertNotIn('LRV-002', result.stdout)
            self.assertNotIn('LRV-004', result.stdout)

    def test_export_marks_changed_commented_hunk_superseded_before_printing(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)
            comment = self.add_parser_comment(repo)
            parser = repo / 'src' / 'parser.py'
            parser.write_text(parser.read_text().replace("return sku or 'UNKNOWN'", "return sku or 'UNKNOWN-SKU'"))

            result = self.run_lrv(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(comment.id, result.stdout)
            self.assertEqual(self.comments_by_id(repo)[comment.id].state, 'superseded')

    def test_export_prints_file_anchor_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            self.make_long_repo_with_early_change(repo)
            app = ReviewApp(repo, load_state(repo))
            app.focus = 'diff'
            rows = app.selected_file_rows()
            app.diff_line = self.row_index_ending(rows, 'LINE_250 = 250')
            app.start_input(1, DummyCurses())
            app.input_text = 'Check this unchanged constant too.'
            app.save_input(DummyCurses())

            result = self.run_lrv(repo, 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LRV-001 long_file.py:250', result.stdout)
            self.assertIn('Reviewed file context:', result.stdout)
            self.assertIn('LINE_250 = 250', result.stdout)

    def test_export_accepts_repository_path(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(ROOT, 'export', str(repo))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LRV-001 src/parser.py:4', result.stdout)

    def test_export_accepts_repository_path_before_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(ROOT, str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LRV-001 src/parser.py:4', result.stdout)

    def test_export_accepts_repo_option(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(ROOT, '--repo', str(repo), 'export')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('### LRV-001 src/parser.py:4', result.stdout)

    def test_show_prints_one_comment_including_non_open_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(repo, 'show', 'LRV-002')

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('# LRV Comment', result.stdout)
            self.assertIn('### LRV-002 src/calculator.py:12', result.stdout)
            self.assertIn('State:\nsuperseded', result.stdout)

    def test_show_unknown_comment_exits_with_error(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            materialize('python-review-basic', repo)

            result = self.run_lrv(repo, 'show', 'LRV-999')

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr.strip(), 'lrv: unknown comment id: LRV-999')


if __name__ == '__main__':
    unittest.main()

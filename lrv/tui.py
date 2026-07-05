import hashlib
import math
import re
import sys

from lrv.git import changed_files, file_diff, file_lines, head_revision
from lrv.state import HunkSnapshot, ReviewState, append_comment, load_state, mark_comments_superseded, remove_comment, save_state, set_comment_state, update_comment_body

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename
    from pygments.token import Comment, Error, Generic, Keyword, Literal, Name, Number, Operator, Punctuation, String
    from pygments.util import ClassNotFound
except Exception:
    lex = None
    get_lexer_for_filename = None
    Comment = Error = Generic = Keyword = Literal = Name = Number = Operator = Punctuation = String = None
    ClassNotFound = Exception


HUNK_RE = re.compile(r'^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@')
SOURCE_ROW_RE = re.compile(r'^( [ 0-9]+ )')
CTRL_D = 4
CTRL_U = 21


def run_tui(repo, state):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(render_review(repo, state), end='')
        return 0

    import curses

    app = ReviewApp(repo, state)
    curses.wrapper(app.run)
    return 0


def render_review(repo, state):
    files = changed_files(repo)
    lines = [
        f'lrv review - HEAD {state.base_commit or head_revision(repo)}',
        '',
        'Changed files:',
    ]
    if not files:
        lines.append('  none')
    for file in files:
        lines.append(f'  {file.status} {file.path}')

    lines.append('')
    lines.append('Comments:')
    if not state.comments:
        lines.append('  none')
    for comment in sorted(state.comments, key=lambda item: item.id):
        lines.append(f'  {comment.id} [{comment.state}] {comment.location()}')
        for body_line in comment.body.splitlines():
            lines.append(f'    {body_line}')

    for file in files:
        lines.extend(['', f'--- {file.status} {file.path} ---'])
        lines.extend(lines_for_file(repo, state, file))

    return '\n'.join(lines) + '\n'


def lines_for_file(repo, state, file):
    return [row.text for row in rows_for_file(repo, state, file)]


def rows_for_file(repo, state, file):
    comments = [comment for comment in state.comments if comment.file == file.path]
    diff_lines = file_diff(repo, file).splitlines()
    if not diff_lines:
        return [RenderedLine('(no diff)', None)]

    rendered = []
    seen = set()
    diff_rows = full_rows_for_file(repo, file, diff_lines)

    for row in diff_rows:
        for comment in matching_row_comments(comments, seen, row, 'before'):
            rendered.extend(RenderedLine(line, None, comment.id) for line in format_inline_comment(comment))
            seen.add(comment.id)

        rendered.append(row)

        for comment in matching_row_comments(comments, seen, row, 'after'):
            rendered.extend(RenderedLine(line, None, comment.id) for line in format_inline_comment(comment))
            seen.add(comment.id)

    for comment in comments:
        if comment.id not in seen:
            rendered.extend(RenderedLine(line, None, comment.id) for line in format_inline_comment(comment))

    return rendered


def refresh_superseded_comments(repo, state):
    if not any(comment.state == 'open' for comment in state.comments):
        return state

    current = current_hunks_by_file(repo)
    superseded = []
    for comment in state.comments:
        if comment.state != 'open':
            continue
        hunks = current.get(comment.file, {})
        current_hash = hunks.get(comment.hunk.header)
        if current_hash != comment.hunk.hash:
            superseded.append(comment.id)

    refreshed = mark_comments_superseded(state, superseded)
    if refreshed is not state:
        save_state(repo, refreshed)
    return refreshed


def current_hunks_by_file(repo):
    current = {}
    for file in changed_files(repo):
        hunks = {}
        for hunk in hunk_snapshots(file_diff(repo, file).splitlines()):
            hunks[hunk.header] = hunk.hash
        current[file.path] = hunks
    return current


def matching_row_comments(comments, seen, row, placement):
    if row.anchor is None:
        return []

    old_target = row.anchor.line if row.anchor.side == 'old' else None
    new_target = row.anchor.line if row.anchor.side == 'new' else None
    return [
        comment
        for comment in comments
        if comment.id not in seen
        and comment.placement == placement
        and comment_matches_line(comment, old_target, new_target)
    ]


def diff_rows_for_file(path, diff_lines):
    hunk_by_index = {}
    for start, end, hunk in hunk_snapshots_with_ranges(diff_lines):
        for index in range(start, end):
            hunk_by_index[index] = hunk

    rendered = []
    old_line = None
    new_line = None

    for index, line in enumerate(diff_lines):
        match = HUNK_RE.match(line)
        if match:
            old_line = int(match.group('old_start'))
            new_line = int(match.group('new_start'))
            rendered.append(RenderedLine(line, None))
            continue

        anchor = None
        if line.startswith('+') and not line.startswith('+++'):
            anchor = DiffAnchor(path, 'new', new_line, hunk_by_index.get(index))
            new_line += 1
            rendered.append(RenderedLine(line, anchor, kind='added'))
            continue
        elif line.startswith('-') and not line.startswith('---'):
            anchor = DiffAnchor(path, 'old', old_line, hunk_by_index.get(index))
            old_line += 1
            rendered.append(RenderedLine(line, anchor, kind='deleted'))
            continue
        elif old_line is not None and new_line is not None:
            anchor = DiffAnchor(path, 'new', new_line, hunk_by_index.get(index))
            old_line += 1
            new_line += 1

        rendered.append(RenderedLine(line, anchor))
    return rendered


def full_rows_for_file(repo, file, diff_lines):
    lines = file_lines(repo, file)
    if lines is None:
        return diff_rows_for_file(file.path, diff_lines)

    anchors = {}
    kinds = {}
    deleted_before = {}
    pending_deleted = []
    for row in diff_rows_for_file(file.path, diff_lines):
        if row.anchor is None:
            continue
        if row.anchor.side == 'old':
            pending_deleted.append(row)
            continue
        if pending_deleted:
            deleted_before.setdefault(row.anchor.line, []).extend(pending_deleted)
            pending_deleted = []
        anchors[row.anchor.line] = row.anchor
        if row.kind == 'added':
            kinds[row.anchor.line] = 'added'

    rendered = []
    for line_number, line in enumerate(lines, start=1):
        rendered.extend(format_deleted_row(row) for row in deleted_before.get(line_number, []))
        kind = kinds.get(line_number, 'unchanged')
        marker = '+' if kind == 'added' else ' '
        rendered.append(RenderedLine(f'{marker}{line_number:>4} {line}', anchors.get(line_number), kind=kind))
    rendered.extend(format_deleted_row(row) for row in pending_deleted)
    return rendered


def format_deleted_row(row):
    text = row.text[1:] if row.text.startswith('-') else row.text
    return RenderedLine(f'-{row.anchor.line:>4} {text}', row.anchor, row.comment_id, kind='deleted')


def minimap_kind(rows):
    kinds = {row.kind for row in rows}
    if 'visual' in kinds:
        return 'visual'
    if any(row.comment_id is not None or row.text.startswith('>>>') for row in rows):
        return 'comment'
    if 'added' in kinds and 'deleted' in kinds:
        return 'mixed'
    if 'deleted' in kinds:
        return 'deleted'
    if 'added' in kinds:
        return 'added'
    return 'unchanged'


def minimap_buckets(rows, height):
    if height <= 0 or not rows:
        return []
    buckets = []
    for index in range(height):
        start = index * len(rows) // height
        end = max(start + 1, (index + 1) * len(rows) // height)
        buckets.append(minimap_kind(rows[start:end]))
    return buckets


def minimap_viewport(total, scroll, visible_height, map_height):
    if total <= 0 or visible_height <= 0 or map_height <= 0:
        return (0, -1)
    start = min(map_height - 1, scroll * map_height // total)
    end = min(map_height - 1, max(start, math.ceil((scroll + visible_height) * map_height / total) - 1))
    return (start, end)


def hunk_snapshots(diff_lines):
    return [hunk for start, end, hunk in hunk_snapshots_with_ranges(diff_lines)]


def hunk_snapshots_with_ranges(diff_lines):
    hunk_ranges = []
    for index, line in enumerate(diff_lines):
        if not HUNK_RE.match(line):
            continue
        end = len(diff_lines)
        for next_index in range(index + 1, len(diff_lines)):
            if HUNK_RE.match(diff_lines[next_index]):
                end = next_index
                break
        hunk_ranges.append((index, end))

    hunks = []
    for start, end in hunk_ranges:
        snapshot = '\n'.join(diff_lines[start:end])
        hunk = HunkSnapshot(
            header=diff_lines[start],
            hash=f'sha256:{hashlib.sha256(snapshot.encode()).hexdigest()}',
            snapshot=snapshot,
        )
        hunks.append((start, end, hunk))
    return hunks


def comment_matches_line(comment, old_line, new_line):
    target = old_line if comment.side == 'old' else new_line
    if target is None:
        return False
    return comment.line_range.start <= target <= comment.line_range.end


def row_matches_anchor(row, anchor):
    if row.anchor is None:
        return False
    return row.anchor.file == anchor.file and row.anchor.side == anchor.side and row.anchor.line == anchor.line


def format_inline_comment(comment):
    lines = [f'>>> {comment.id} [{comment.state}] {comment.location()}']
    for body_line in comment.body.splitlines():
        lines.append(f'>>> {body_line}')
    return lines


def source_row_parts(row):
    if row.comment_id is not None or row.kind != 'unchanged':
        return None
    match = SOURCE_ROW_RE.match(row.text)
    if not match:
        return None
    return match.group(1), row.text[match.end():]


def syntax_token_kind(token):
    if Comment is not None and token in Comment:
        return 'comment'
    if Error is not None and token in Error:
        return 'error'
    if Keyword is not None and token in Keyword:
        return 'keyword'
    if String is not None and token in String:
        return 'string'
    if Number is not None and token in Number:
        return 'number'
    if Name is not None and token in Name.Decorator:
        return 'decorator'
    if Name is not None and token in Name.Function:
        return 'function'
    if Name is not None and token in Name.Class:
        return 'class'
    if Name is not None and token in Name.Builtin:
        return 'builtin'
    if Name is not None and token in Name.Constant:
        return 'constant'
    if Name is not None and token in Name.Namespace:
        return 'namespace'
    if Name is not None and token in Name.Attribute:
        return 'attribute'
    if Name is not None and token in Name.Variable:
        return 'variable'
    if Operator is not None and token in Operator:
        return 'operator'
    if Punctuation is not None and token in Punctuation:
        return 'punctuation'
    if Generic is not None and token in Generic:
        return 'generic'
    if Literal is not None and token in Literal:
        return 'literal'
    return None


def syntax_spans(path, code):
    if lex is None or get_lexer_for_filename is None:
        return [(code, None)]
    try:
        lexer = get_lexer_for_filename(path)
    except ClassNotFound:
        return [(code, None)]
    return [(text, syntax_token_kind(token)) for token, text in lex(code, lexer) if text]


class RenderedLine:
    def __init__(self, text, anchor, comment_id=None, kind='normal'):
        self.text = text
        self.anchor = anchor
        self.comment_id = comment_id
        self.kind = kind


class DiffAnchor:
    def __init__(self, file, side, line, hunk):
        self.file = file
        self.side = side
        self.line = line
        self.hunk = hunk


class ReviewApp:
    def __init__(self, repo, state):
        self.repo = repo
        self.state = state
        self.files = []
        self.selected = 0
        self.scroll = 0
        self.focus = 'files'
        self.diff_line = 0
        self.mode = 'normal'
        self.input_text = ''
        self.input_anchor = None
        self.input_end_line = None
        self.input_comment_id = None
        self.input_placement = 'after'
        self.visual_anchor = None
        self.status_message = ''
        self.visible_diff_height = 10
        self.modal_comment_ids = ()
        self.modal_original_states = {}
        self.modal_index = 0
        self.modal_scroll = 0
        self.syntax_colors = False
        self.reload()

    def reload(self, target_anchor=None, target_scroll=None):
        selected_path = self.files[self.selected].path if self.files and self.selected < len(self.files) else None
        self.state = load_state(self.repo)
        self.state = refresh_superseded_comments(self.repo, self.state)
        self.files = changed_files(self.repo)
        if selected_path is not None:
            for index, file in enumerate(self.files):
                if file.path == selected_path:
                    self.selected = index
                    break
        if self.selected >= len(self.files):
            self.selected = max(0, len(self.files) - 1)
        if target_scroll is None:
            self.scroll = 0
        else:
            self.scroll = target_scroll
        if target_anchor is None:
            self.diff_line = 0
        else:
            self.diff_line = self.anchor_row_index(target_anchor)

    def run(self, screen):
        import curses

        self.set_cursor(curses, 0)
        screen.keypad(True)
        self.init_colors(curses)

        while True:
            self.draw(screen, curses)
            key = screen.getch()
            if self.mode == 'input':
                self.handle_input_key(key, curses)
                continue
            if self.mode == 'comments':
                self.handle_modal_key(key, curses)
                continue
            if self.mode == 'visual':
                self.handle_visual_key(key, curses)
                continue
            if key in (ord('q'), 27):
                return
            if key in (ord('r'),):
                self.reload()
            elif key in (ord('\t'),):
                self.toggle_focus()
            elif key in (curses.KEY_UP, ord('k')):
                self.move_up()
            elif key in (curses.KEY_DOWN, ord('j')):
                self.move_down()
            elif key in (curses.KEY_PPAGE, ord('b')):
                self.page_diff(-10)
            elif key in (curses.KEY_NPAGE, ord('f')):
                self.page_diff(10)
            elif key == ord(' '):
                self.open_comment_modal()
            elif key == CTRL_D:
                self.half_page_diff(1)
            elif key == CTRL_U:
                self.half_page_diff(-1)
            elif key in (curses.KEY_LEFT, ord('h')):
                self.focus = 'files'
            elif key in (curses.KEY_RIGHT, ord('l')):
                self.focus = 'diff'
            elif key == ord('o'):
                self.start_input(1, curses)
            elif key == ord('O'):
                self.start_input(-1, curses)
            elif key in (ord('v'), ord('V')):
                self.start_visual()
            elif key == ord('i'):
                self.start_comment_edit(curses)
            elif key == ord('d'):
                self.delete_selected_comment()

    def init_colors(self, curses):
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        curses.init_pair(5, curses.COLOR_YELLOW, -1)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)
        self.init_color_pair(curses, 9, curses.COLOR_CYAN, -1)
        self.init_color_pair(curses, 10, curses.COLOR_YELLOW, -1)
        self.init_color_pair(curses, 11, curses.COLOR_MAGENTA if curses.COLORS > 7 else curses.COLOR_CYAN, -1)
        self.init_color_pair(curses, 12, curses.COLOR_BLUE, -1)
        self.init_color_pair(curses, 13, curses.COLOR_WHITE, -1)
        self.syntax_colors = lex is not None

    def init_color_pair(self, curses, pair, foreground, background):
        try:
            curses.init_pair(pair, foreground, background)
        except Exception:
            pass

    def move_selected(self, delta):
        if not self.files:
            return
        self.selected = max(0, min(len(self.files) - 1, self.selected + delta))
        self.scroll = 0
        self.diff_line = 0

    def toggle_focus(self):
        self.focus = 'diff' if self.focus == 'files' else 'files'

    def move_up(self):
        if self.mode == 'visual':
            self.move_visual_line(-1)
            return
        if self.focus == 'files':
            self.move_selected(-1)
        else:
            self.move_diff_line(-1)

    def move_down(self):
        if self.mode == 'visual':
            self.move_visual_line(1)
            return
        if self.focus == 'files':
            self.move_selected(1)
        else:
            self.move_diff_line(1)

    def move_diff_line(self, delta):
        lines = self.selected_file_lines()
        if not lines:
            return
        self.diff_line = max(0, min(len(lines) - 1, self.diff_line + delta))

    def page_diff(self, delta):
        if self.focus == 'files':
            self.focus = 'diff'
        self.move_diff_line(delta)

    def half_page_diff(self, direction):
        if self.focus != 'diff':
            return
        amount = max(1, self.visible_diff_height // 2)
        self.move_diff_line(amount * direction)

    def start_visual(self):
        if not self.files:
            return
        rows = self.selected_file_rows()
        if not rows:
            self.status_message = 'No reviewable line selected.'
            return
        anchor_index = max(0, min(len(rows) - 1, self.diff_line))
        anchor = rows[anchor_index].anchor
        if anchor is None or anchor.hunk is None:
            self.status_message = 'No reviewable line selected.'
            return
        self.focus = 'diff'
        self.diff_line = anchor_index
        self.visual_anchor = anchor_index
        self.mode = 'visual'
        self.status_message = ''

    def cancel_visual(self):
        self.mode = 'normal'
        self.visual_anchor = None

    def handle_visual_key(self, key, curses):
        if key == 27:
            self.cancel_visual()
        elif key in (curses.KEY_UP, ord('k')):
            self.move_visual_line(-1)
        elif key in (curses.KEY_DOWN, ord('j')):
            self.move_visual_line(1)
        elif key in (curses.KEY_PPAGE, ord('b')):
            self.page_diff(-10)
        elif key in (curses.KEY_NPAGE, ord('f')):
            self.page_diff(10)
        elif key == CTRL_D:
            self.half_page_diff(1)
        elif key == CTRL_U:
            self.half_page_diff(-1)
        elif key == ord('i'):
            self.start_visual_input(curses)

    def move_visual_line(self, delta):
        rows = self.selected_file_rows()
        if not rows:
            return
        index = max(0, min(len(rows) - 1, self.diff_line)) + delta
        while 0 <= index < len(rows):
            if rows[index].anchor is not None:
                self.diff_line = index
                return
            index += delta

    def start_visual_input(self, curses):
        selection = self.visual_selection()
        if selection is None:
            return
        anchor, end_line = selection
        self.input_anchor = anchor
        self.input_end_line = end_line
        self.input_comment_id = None
        self.input_text = ''
        self.input_placement = 'after'
        self.mode = 'input'
        self.set_cursor(curses, 1)

    def visual_selection(self):
        rows = self.selected_file_rows()
        if self.visual_anchor is None or not rows:
            self.status_message = 'No visual selection.'
            return None
        start = max(0, min(len(rows) - 1, self.visual_anchor))
        end = max(0, min(len(rows) - 1, self.diff_line))
        if start > end:
            start, end = end, start
        anchors = [row.anchor for row in rows[start:end + 1] if row.anchor is not None]
        if not anchors:
            self.status_message = 'Selection has no reviewable lines.'
            return None
        first = anchors[0]
        if first.hunk is None:
            self.status_message = 'Selection must be inside a hunk.'
            return None
        for anchor in anchors:
            if anchor.hunk is None or anchor.hunk.header != first.hunk.header or anchor.hunk.hash != first.hunk.hash:
                self.status_message = 'Range comments cannot cross hunks.'
                return None
            if anchor.side != first.side:
                self.status_message = 'Range comments cannot mix old and new lines.'
                return None
        lines = sorted(anchor.line for anchor in anchors)
        expected = list(range(lines[0], lines[-1] + 1))
        if lines != expected:
            self.status_message = 'Range comments must target contiguous lines.'
            return None
        self.status_message = ''
        return DiffAnchor(first.file, first.side, lines[0], first.hunk), lines[-1]

    def visual_row_indexes(self):
        rows = self.selected_file_rows()
        if self.mode not in ('visual', 'input') or self.visual_anchor is None or not rows:
            return set()
        start = max(0, min(len(rows) - 1, self.visual_anchor))
        end = max(0, min(len(rows) - 1, self.diff_line))
        if start > end:
            start, end = end, start
        return set(range(start, end + 1))

    def rows_with_visual_selection(self, rows):
        indexes = self.visual_row_indexes()
        if not indexes:
            return rows
        rendered = []
        for index, row in enumerate(rows):
            kind = 'visual' if index in indexes and row.anchor is not None else row.kind
            rendered.append(RenderedLine(row.text, row.anchor, row.comment_id, kind))
        return rendered

    def diff_progress_label(self):
        lines = self.selected_file_lines()
        if not lines:
            return '0%'
        if self.diff_line <= 0:
            return 'Top'
        if self.diff_line >= len(lines) - 1:
            return 'Bot'
        return f'{round((self.diff_line + 1) * 100 / len(lines))}%'

    def selected_file_lines(self):
        return [row.text for row in self.selected_file_rows()]

    def selected_file_rows(self):
        if not self.files:
            return []
        return rows_for_file(self.repo, self.state, self.files[self.selected])

    def open_comment_modal(self):
        ids = [
            comment.id
            for comment in sorted(self.state.comments, key=lambda item: item.id)
            if comment.state in ('open', 'superseded')
        ]
        self.modal_comment_ids = tuple(ids)
        self.modal_original_states = {comment.id: comment.state for comment in self.state.comments if comment.id in ids}
        self.modal_index = 0
        self.modal_scroll = 0
        self.mode = 'comments'

    def close_comment_modal(self):
        self.mode = 'normal'
        self.modal_comment_ids = ()
        self.modal_original_states = {}
        self.modal_index = 0
        self.modal_scroll = 0

    def modal_comments(self):
        by_id = {comment.id: comment for comment in self.state.comments}
        return [by_id[id] for id in self.modal_comment_ids if id in by_id]

    def selected_modal_comment(self):
        comments = self.modal_comments()
        if not comments:
            return None
        self.modal_index = max(0, min(len(comments) - 1, self.modal_index))
        return comments[self.modal_index]

    def move_modal_selection(self, delta):
        comments = self.modal_comments()
        if not comments:
            self.modal_index = 0
            return
        self.modal_index = max(0, min(len(comments) - 1, self.modal_index + delta))

    def handle_modal_key(self, key, curses):
        if key in (27, ord('q')):
            self.close_comment_modal()
        elif key in (curses.KEY_UP, ord('k')):
            self.move_modal_selection(-1)
        elif key in (curses.KEY_DOWN, ord('j')):
            self.move_modal_selection(1)
        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            self.jump_to_modal_comment()
            self.close_comment_modal()
        elif key == ord('r'):
            self.transition_selected_modal_comment('resolved')
        elif key == ord('x'):
            self.transition_selected_modal_comment('dismissed')

    def transition_selected_modal_comment(self, comment_state):
        comment = self.selected_modal_comment()
        if comment is None:
            return
        original_state = self.modal_original_states.get(comment.id, comment.state)
        next_state = original_state if comment.state == comment_state else comment_state
        self.state = set_comment_state(self.state, comment.id, next_state)
        save_state(self.repo, self.state)

    def jump_to_modal_comment(self):
        comment = self.selected_modal_comment()
        if comment is None:
            return
        for index, file in enumerate(self.files):
            if file.path == comment.file:
                self.selected = index
                self.focus = 'diff'
                self.diff_line = self.comment_row_index(comment)
                return

    def comment_row_index(self, comment):
        rows = rows_for_file(self.repo, self.state, self.files[self.selected])
        for index, row in enumerate(rows):
            if row.comment_id == comment.id:
                return index
        for index, row in enumerate(rows):
            if row.anchor is None:
                continue
            old_line = row.anchor.line if row.anchor.side == 'old' else None
            new_line = row.anchor.line if row.anchor.side == 'new' else None
            if comment_matches_line(comment, old_line, new_line):
                return index
        return 0

    def anchor_row_index(self, anchor):
        rows = self.selected_file_rows()
        for index, row in enumerate(rows):
            if row_matches_anchor(row, anchor):
                return index
        return max(0, min(len(rows) - 1, self.diff_line))

    def start_input(self, direction, curses):
        if not self.files:
            return
        anchor_index = self.comment_anchor_index(direction)
        if anchor_index is None:
            return
        self.focus = 'diff'
        self.diff_line = anchor_index
        rows = self.selected_file_rows()
        self.input_anchor = rows[anchor_index].anchor
        if self.input_anchor is None or self.input_anchor.hunk is None:
            return
        self.input_text = ''
        self.input_comment_id = None
        self.input_placement = 'before' if direction < 0 else 'after'
        self.mode = 'input'
        self.set_cursor(curses, 1)

    def start_comment_edit(self, curses):
        comment = self.selected_inline_comment()
        if comment is None:
            return
        self.input_comment_id = comment.id
        self.input_text = comment.body
        self.input_anchor = DiffAnchor(comment.file, comment.side, comment.line_range.start, comment.hunk)
        self.input_end_line = comment.line_range.end
        self.input_placement = comment.placement
        self.mode = 'input'
        self.set_cursor(curses, 1)

    def selected_inline_comment(self):
        rows = self.selected_file_rows()
        if not rows:
            return None
        index = max(0, min(len(rows) - 1, self.diff_line))
        comment_id = rows[index].comment_id
        if comment_id is None:
            return None
        for comment in self.state.comments:
            if comment.id == comment_id:
                return comment
        return None

    def comment_anchor_index(self, direction):
        rows = self.selected_file_rows()
        if not rows:
            return None
        start = max(0, min(len(rows) - 1, self.diff_line))
        if rows[start].anchor is not None:
            return start

        index = start + direction
        while 0 <= index < len(rows):
            if rows[index].anchor is not None:
                return index
            index += direction

        fallback = range(len(rows)) if direction < 0 else range(len(rows) - 1, -1, -1)
        for index in fallback:
            if rows[index].anchor is not None:
                return index
        return None

    def handle_input_key(self, key, curses):
        if key == 27:
            self.cancel_input(curses)
        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            self.save_input(curses)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.input_text = self.input_text[:-1]
        elif 32 <= key <= 126:
            self.input_text += chr(key)

    def cancel_input(self, curses):
        self.mode = 'normal'
        self.input_text = ''
        self.input_anchor = None
        self.input_end_line = None
        self.input_comment_id = None
        self.input_placement = 'after'
        self.visual_anchor = None
        self.set_cursor(curses, 0)

    def save_input(self, curses):
        body = self.input_text.strip()
        if body:
            anchor = self.input_anchor
            scroll = self.scroll
            if self.input_comment_id is not None:
                self.state = update_comment_body(self.state, self.input_comment_id, body)
                save_state(self.repo, self.state)
                self.cancel_input(curses)
                self.reload(target_anchor=anchor, target_scroll=scroll)
                return
            state = self.state_with_base_commit()
            self.state = append_comment(
                state,
                anchor.file,
                anchor.side,
                anchor.line,
                anchor.hunk,
                body,
                placement=self.input_placement,
                end_line=self.input_end_line,
            )
            save_state(self.repo, self.state)
            self.cancel_input(curses)
            self.reload(target_anchor=anchor, target_scroll=scroll)
            return
        self.cancel_input(curses)

    def delete_selected_comment(self):
        rows = self.selected_file_rows()
        if not rows:
            return
        index = max(0, min(len(rows) - 1, self.diff_line))
        comment_id = rows[index].comment_id
        if comment_id is None:
            return
        self.state = remove_comment(self.state, comment_id)
        save_state(self.repo, self.state)
        self.diff_line = min(index, max(0, len(self.selected_file_rows()) - 1))

    def state_with_base_commit(self):
        if self.state.base_commit:
            return self.state
        return ReviewState(
            version=self.state.version,
            repo_root=str(self.repo),
            base_commit=head_revision(self.repo),
            comments=self.state.comments,
        )

    def clamp_scroll(self, visible_height):
        if visible_height <= 0:
            return
        if self.diff_line < self.scroll:
            self.scroll = self.diff_line
        elif self.diff_line >= self.scroll + visible_height:
            self.scroll = self.diff_line - visible_height + 1
        self.scroll = max(0, self.scroll)

    def draw(self, screen, curses):
        screen.erase()
        height, width = screen.getmaxyx()
        sidebar_width = min(36, max(22, width // 3))
        main_x = sidebar_width + 1

        self.draw_header(screen, width, curses)
        self.draw_sidebar(screen, height, sidebar_width, curses)
        self.draw_main(screen, height, width, main_x, curses)
        self.draw_input(screen, height, width, curses)
        self.draw_comment_modal(screen, height, width, curses)
        screen.refresh()

    def draw_header(self, screen, width, curses):
        labels = {
            'input': 'INPUT',
            'comments': 'COMMENTS',
            'visual': 'VISUAL',
        }
        mode = labels.get(self.mode, 'NORMAL')
        title = f'lrv review  {mode}  HEAD {self.state.base_commit or head_revision(self.repo)}  tab focus  space comments  v visual  i comment  o/O line  d delete  q quit  r refresh'
        attr = curses.color_pair(7) if self.mode in ('input', 'visual') else curses.color_pair(6)
        if attr == 0:
            attr = curses.A_REVERSE
        self.addstr(screen, 0, 0, title[:width - 1].ljust(max(0, width - 1)), attr)

    def draw_sidebar(self, screen, height, width, curses):
        attr = curses.A_BOLD | (curses.A_UNDERLINE if self.focus == 'files' else curses.A_NORMAL)
        self.addstr(screen, 2, 1, 'Changed files', attr)
        if not self.files:
            self.addstr(screen, 4, 1, 'none')
            return

        for index, file in enumerate(self.files[:height - 4]):
            attr = curses.color_pair(1) if index == self.selected and self.focus == 'files' else curses.A_NORMAL
            if index == self.selected and self.focus == 'diff':
                attr = curses.A_BOLD
            label = f'{file.status} {file.path}'
            self.addstr(screen, index + 4, 1, label[:width - 2], attr)

    def draw_main(self, screen, height, width, x, curses):
        if not self.files:
            self.addstr(screen, 2, x, 'No changes in working tree.')
            return

        file = self.files[self.selected]
        title = f'{file.status} {file.path}'
        attr = curses.A_BOLD | (curses.A_UNDERLINE if self.focus == 'diff' else curses.A_NORMAL)
        self.addstr(screen, 2, x, title[:width - x - 1], attr)
        rows = self.selected_file_rows()
        display_rows = self.rows_with_visual_selection(rows)
        visible_height = max(0, height - 5)
        if self.mode == 'input':
            visible_height = max(0, visible_height - 1)
        self.visible_diff_height = visible_height
        self.clamp_scroll(visible_height)
        visible = display_rows[self.scroll:self.scroll + visible_height]
        minimap_x = self.minimap_x(width, x)
        text_width = minimap_x - x - 1 if minimap_x is not None else width - x - 1

        for index, row in enumerate(visible):
            attr = self.line_attr(curses, row)
            selected = self.focus == 'diff' and self.scroll + index == self.diff_line
            if self.draw_source_row(screen, index + 4, x, row, file.path, text_width, attr, selected, curses):
                continue
            if self.focus == 'diff' and self.scroll + index == self.diff_line:
                attr |= self.current_line_attr(curses)
            self.addstr(screen, index + 4, x, row.text[:text_width], attr)

        if minimap_x is not None:
            self.draw_minimap(screen, display_rows, 4, minimap_x, visible_height, curses)

        footer = self.status_message or self.diff_progress_label()
        self.addstr(screen, height - 1, x, footer[:max(0, width - x - 1)], curses.A_BOLD)

    def minimap_x(self, width, main_x):
        if width - main_x < 44:
            return None
        return width - 3

    def draw_minimap(self, screen, rows, y, x, height, curses):
        buckets = minimap_buckets(rows, height)
        view_start, view_end = minimap_viewport(len(rows), self.scroll, height, len(buckets))
        for index, kind in enumerate(buckets):
            marker = '#' if kind != 'unchanged' else '.'
            pointer = '>' if view_start <= index <= view_end else ' '
            self.addstr(screen, y + index, x, pointer, curses.A_BOLD if pointer == '>' else curses.A_DIM)
            self.addstr(screen, y + index, x + 1, marker, self.minimap_attr(curses, kind))

    def minimap_attr(self, curses, kind):
        if kind == 'visual':
            return curses.color_pair(8) | curses.A_BOLD
        if kind == 'comment':
            return curses.color_pair(5) | curses.A_BOLD
        if kind == 'mixed':
            return curses.color_pair(4) | curses.A_BOLD
        if kind == 'added':
            return curses.color_pair(2)
        if kind == 'deleted':
            return curses.color_pair(3)
        return curses.A_DIM

    def draw_input(self, screen, height, width, curses):
        if self.mode != 'input' or height <= 1:
            return
        prompt = f'>>> {self.input_text}'
        attr = curses.color_pair(5) | curses.A_BOLD
        self.addstr(screen, height - 1, 0, prompt[:width - 1].ljust(max(0, width - 1)), attr)
        try:
            screen.move(height - 1, min(width - 2, len(prompt)))
        except Exception:
            pass

    def draw_comment_modal(self, screen, height, width, curses):
        if self.mode != 'comments':
            return
        modal_height = min(max(7, height - 4), 18)
        modal_width = min(max(50, width - 8), 88)
        y = max(1, (height - modal_height) // 2)
        x = max(0, (width - modal_width) // 2)
        bottom = y + modal_height - 1
        right = x + modal_width - 1

        title = 'Comments  enter jump  r resolve  x dismiss  esc close'
        self.addstr(screen, y, x, '+' + '-' * (modal_width - 2) + '+', curses.A_BOLD)
        self.addstr(screen, y + 1, x, '|', curses.A_BOLD)
        self.addstr(screen, y + 1, x + 2, title[:modal_width - 4], curses.A_BOLD)
        self.addstr(screen, y + 1, right, '|', curses.A_BOLD)
        self.addstr(screen, y + 2, x, '+' + '-' * (modal_width - 2) + '+', curses.A_BOLD)

        comments = self.modal_comments()
        visible_height = max(0, modal_height - 4)
        if self.modal_index < self.modal_scroll:
            self.modal_scroll = self.modal_index
        elif self.modal_index >= self.modal_scroll + visible_height:
            self.modal_scroll = self.modal_index - visible_height + 1
        self.modal_scroll = max(0, self.modal_scroll)
        visible = comments[self.modal_scroll:self.modal_scroll + visible_height]

        for offset in range(visible_height):
            row_y = y + 3 + offset
            self.addstr(screen, row_y, x, '|')
            self.addstr(screen, row_y, right, '|')
            self.addstr(screen, row_y, x + 1, ' ' * max(0, modal_width - 2))

        if not comments:
            self.addstr(screen, y + 3, x + 2, 'No open or superseded comments.')
        for offset, comment in enumerate(visible):
            index = self.modal_scroll + offset
            label = f'{comment.id} [{comment.state}] {comment.location()}  {comment.body.splitlines()[0]}'
            attr = self.modal_comment_attr(curses, comment)
            if index == self.modal_index:
                attr |= curses.A_REVERSE
            self.addstr(screen, y + 3 + offset, x + 2, label[:modal_width - 4], attr)

        self.addstr(screen, bottom, x, '+' + '-' * (modal_width - 2) + '+', curses.A_BOLD)

    def modal_comment_attr(self, curses, comment):
        if comment.state == 'open':
            return curses.color_pair(5) | curses.A_BOLD
        if comment.state == 'superseded':
            return curses.color_pair(4)
        if comment.state == 'resolved':
            return curses.color_pair(2)
        if comment.state == 'dismissed':
            return curses.color_pair(3)
        return curses.A_NORMAL

    def line_attr(self, curses, row):
        line = row.text
        if line.startswith('>>>'):
            return curses.color_pair(5) | curses.A_BOLD
        if row.kind == 'visual':
            return curses.color_pair(8) | curses.A_BOLD
        if row.kind == 'added':
            return curses.color_pair(2)
        if row.kind == 'deleted':
            return curses.color_pair(3)
        if row.kind == 'unchanged':
            return curses.A_NORMAL
        if line.startswith('+') and not line.startswith('+++'):
            return curses.color_pair(2)
        if line.startswith('-') and not line.startswith('---'):
            return curses.color_pair(3)
        if line.startswith('@@'):
            return curses.color_pair(4)
        return curses.A_NORMAL

    def current_line_attr(self, curses):
        return curses.A_BOLD | curses.A_UNDERLINE

    def draw_source_row(self, screen, y, x, row, path, width, base_attr, selected, curses):
        if not getattr(self, 'syntax_colors', False):
            return False
        parts = source_row_parts(row)
        if parts is None or width <= 0:
            return False
        prefix, code = parts
        selected_attr = self.current_line_attr(curses) if selected else curses.A_NORMAL
        attr = base_attr | selected_attr
        self.addstr(screen, y, x, prefix[:width], attr)
        column = x + min(len(prefix), width)
        remaining = width - min(len(prefix), width)
        if remaining <= 0:
            return True
        for text, kind in syntax_spans(path, code):
            if remaining <= 0:
                break
            value = text[:remaining]
            span_attr = self.syntax_attr(curses, kind, base_attr)
            if selected:
                span_attr |= selected_attr
            self.addstr(screen, y, column, value, span_attr)
            column += len(value)
            remaining -= len(value)
        return True

    def syntax_attr(self, curses, kind, base_attr):
        if kind == 'comment':
            return curses.A_DIM
        if kind == 'error':
            return curses.color_pair(3) | curses.A_BOLD
        if kind == 'keyword':
            return curses.color_pair(9) | curses.A_BOLD
        if kind == 'string':
            return curses.color_pair(10)
        if kind in ('number', 'literal'):
            return curses.color_pair(11)
        if kind in ('function', 'class', 'decorator'):
            return curses.color_pair(12) | curses.A_BOLD
        if kind in ('builtin', 'constant'):
            return curses.color_pair(11) | curses.A_BOLD
        if kind in ('namespace', 'attribute'):
            return curses.color_pair(9)
        if kind == 'variable':
            return curses.color_pair(13)
        if kind in ('operator', 'punctuation'):
            return curses.color_pair(13) | curses.A_BOLD
        if kind == 'generic':
            return curses.color_pair(4)
        return base_attr

    def addstr(self, screen, y, x, value, attr=0):
        try:
            screen.addstr(y, x, value, attr)
        except Exception:
            pass

    def set_cursor(self, curses, visible):
        try:
            curses.curs_set(visible)
        except Exception:
            pass

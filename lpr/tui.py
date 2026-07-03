import hashlib
import re
import sys

from lpr.git import changed_files, file_diff, head_revision
from lpr.state import HunkSnapshot, ReviewState, append_comment, load_state, remove_comment, save_state


HUNK_RE = re.compile(r'^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@')
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
        f'lpr review - HEAD {state.base_commit or head_revision(repo)}',
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
    diff_rows = diff_rows_for_file(file.path, diff_lines)

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

    hunk_by_index = {}
    for start, end in hunk_ranges:
        snapshot = '\n'.join(diff_lines[start:end])
        hunk = HunkSnapshot(
            header=diff_lines[start],
            hash=f'sha256:{hashlib.sha256(snapshot.encode()).hexdigest()}',
            snapshot=snapshot,
        )
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
        elif line.startswith('-') and not line.startswith('---'):
            anchor = DiffAnchor(path, 'old', old_line, hunk_by_index.get(index))
            old_line += 1
        elif old_line is not None and new_line is not None:
            anchor = DiffAnchor(path, 'new', new_line, hunk_by_index.get(index))
            old_line += 1
            new_line += 1

        rendered.append(RenderedLine(line, anchor))
    return rendered


def comment_matches_line(comment, old_line, new_line):
    target = old_line if comment.side == 'old' else new_line
    if target is None:
        return False
    return comment.line_range.start <= target <= comment.line_range.end


def format_inline_comment(comment):
    lines = [f'>>> {comment.id} [{comment.state}] {comment.location()}']
    for body_line in comment.body.splitlines():
        lines.append(f'>>> {body_line}')
    return lines


class RenderedLine:
    def __init__(self, text, anchor, comment_id=None):
        self.text = text
        self.anchor = anchor
        self.comment_id = comment_id


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
        self.input_placement = 'after'
        self.visible_diff_height = 10
        self.reload()

    def reload(self):
        self.state = load_state(self.repo)
        self.files = changed_files(self.repo)
        if self.selected >= len(self.files):
            self.selected = max(0, len(self.files) - 1)
        self.scroll = 0
        self.diff_line = 0

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
            elif key in (curses.KEY_NPAGE, ord('f'), ord(' ')):
                self.page_diff(10)
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

    def move_selected(self, delta):
        if not self.files:
            return
        self.selected = max(0, min(len(self.files) - 1, self.selected + delta))
        self.scroll = 0
        self.diff_line = 0

    def toggle_focus(self):
        self.focus = 'diff' if self.focus == 'files' else 'files'

    def move_up(self):
        if self.focus == 'files':
            self.move_selected(-1)
        else:
            self.move_diff_line(-1)

    def move_down(self):
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
        self.input_placement = 'before' if direction < 0 else 'after'
        self.mode = 'input'
        self.set_cursor(curses, 1)

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
        self.input_placement = 'after'
        self.set_cursor(curses, 0)

    def save_input(self, curses):
        body = self.input_text.strip()
        if body:
            state = self.state_with_base_commit()
            self.state = append_comment(
                state,
                self.input_anchor.file,
                self.input_anchor.side,
                self.input_anchor.line,
                self.input_anchor.hunk,
                body,
                placement=self.input_placement,
            )
            save_state(self.repo, self.state)
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
        screen.refresh()

    def draw_header(self, screen, width, curses):
        mode = 'INPUT' if self.mode == 'input' else 'NORMAL'
        title = f'lpr review  {mode}  HEAD {self.state.base_commit or head_revision(self.repo)}  tab focus  o/O comment  d delete  q quit  r refresh'
        attr = curses.color_pair(7) if self.mode == 'input' else curses.color_pair(6)
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
        lines = self.selected_file_lines()
        visible_height = max(0, height - 5)
        if self.mode == 'input':
            visible_height = max(0, visible_height - 1)
        self.visible_diff_height = visible_height
        self.clamp_scroll(visible_height)
        visible = lines[self.scroll:self.scroll + visible_height]

        for index, line in enumerate(visible):
            attr = self.line_attr(curses, line)
            if self.focus == 'diff' and self.scroll + index == self.diff_line:
                attr |= curses.A_REVERSE
            self.addstr(screen, index + 4, x, line[:width - x - 1], attr)

        progress = self.diff_progress_label()
        self.addstr(screen, height - 1, max(x, width - len(progress) - 1), progress, curses.A_BOLD)

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

    def line_attr(self, curses, line):
        if line.startswith('>>>'):
            return curses.color_pair(5) | curses.A_BOLD
        if line.startswith('+') and not line.startswith('+++'):
            return curses.color_pair(2)
        if line.startswith('-') and not line.startswith('---'):
            return curses.color_pair(3)
        if line.startswith('@@'):
            return curses.color_pair(4)
        return curses.A_NORMAL

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

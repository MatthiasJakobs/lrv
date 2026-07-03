import re
import sys

from lpr.git import changed_files, file_diff, head_revision
from lpr.state import load_state


HUNK_RE = re.compile(r'^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@')


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
    comments = [comment for comment in state.comments if comment.file == file.path]
    diff_lines = file_diff(repo, file).splitlines()
    if not diff_lines:
        return ['(no diff)']

    rendered = []
    seen = set()
    old_line = None
    new_line = None

    for line in diff_lines:
        rendered.append(line)
        match = HUNK_RE.match(line)
        if match:
            old_line = int(match.group('old_start'))
            new_line = int(match.group('new_start'))
            continue

        old_target = None
        new_target = None
        if line.startswith('+') and not line.startswith('+++'):
            new_target = new_line
            new_line += 1
        elif line.startswith('-') and not line.startswith('---'):
            old_target = old_line
            old_line += 1
        elif old_line is not None and new_line is not None:
            old_target = old_line
            new_target = new_line
            old_line += 1
            new_line += 1

        for comment in comments:
            if comment.id in seen:
                continue
            if comment_matches_line(comment, old_target, new_target):
                rendered.extend(format_inline_comment(comment))
                seen.add(comment.id)

    for comment in comments:
        if comment.id not in seen:
            rendered.extend(format_inline_comment(comment))

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


class ReviewApp:
    def __init__(self, repo, state):
        self.repo = repo
        self.state = state
        self.files = []
        self.selected = 0
        self.scroll = 0
        self.focus = 'files'
        self.diff_line = 0
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

        curses.curs_set(0)
        screen.keypad(True)
        self.init_colors(curses)

        while True:
            self.draw(screen, curses)
            key = screen.getch()
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
            elif key in (curses.KEY_LEFT, ord('h')):
                self.focus = 'files'
            elif key in (curses.KEY_RIGHT, ord('l')):
                self.focus = 'diff'

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

    def selected_file_lines(self):
        if not self.files:
            return []
        return lines_for_file(self.repo, self.state, self.files[self.selected])

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
        screen.refresh()

    def draw_header(self, screen, width, curses):
        title = f'lpr review  HEAD {self.state.base_commit or head_revision(self.repo)}  tab focus  q quit  r refresh'
        self.addstr(screen, 0, 0, title[:width - 1], curses.A_REVERSE)

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
        self.clamp_scroll(visible_height)
        visible = lines[self.scroll:self.scroll + visible_height]

        for index, line in enumerate(visible):
            attr = self.line_attr(curses, line)
            if self.focus == 'diff' and self.scroll + index == self.diff_line:
                attr |= curses.A_REVERSE
            self.addstr(screen, index + 4, x, line[:width - x - 1], attr)

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

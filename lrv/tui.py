import hashlib
import math
import os
import re
import sys
import time

from lrv.git import ChangedFile, changed_files, file_diff, file_lines, head_revision
from lrv.state import FileAnchor, HunkSnapshot, ReviewState, append_comment, load_state, mark_comments_superseded, remove_comment, save_state, set_comment_state, update_comment_body, update_comment_line_range
from lrv.theme import CUSTOM_COLOR_IDS, FALLBACK_COLOR_NAMES, default_theme, hex_to_curses_rgb, hex_to_xterm_256, load_theme

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
SOURCE_ROW_RE = re.compile(r'^([ +-] *\d+ )')
CTRL_D = 4
CTRL_U = 21
FILE_ANCHOR_CONTEXT = 3
HOT_RELOAD_INTERVAL = 1.0  # seconds between automatic reloads
CHANGED_BG_PAIRS = {
    'added': 14,
    'deleted': 15,
}
COMMENT_TARGET_BG_PAIRS = {
    'open': 38,
    'superseded': 39,
}
CURRENT_LINE_BG_PAIR = 40
COMMENT_STATE_FG_PAIRS = {
    'open': 5,
    'superseded': 41,
    'resolved': 42,
    'dismissed': 43,
}
SYNTAX_PLAIN_PAIRS = {
    'builtin': 44,
    'constant': 44,
    'operator': 45,
    'punctuation': 45,
    'generic': 46,
}
CHANGED_BG_COLORS = {
    'added': 22,
    'deleted': 52,
}
COMMENT_TARGET_BG_COLORS = {
    'open': 58,
    'superseded': 24,
}
CURRENT_LINE_BG_COLOR = 236
SIDEBAR_FILE_LABEL_WIDTH = 18
SIDEBAR_COUNT_WIDTH = 4
UNICODE_GLYPHS = {
    'vertical': '│',
    'top_left': '┌',
    'top_right': '┐',
    'bottom_left': '└',
    'bottom_right': '┘',
    'horizontal': '─',
}
ASCII_GLYPHS = {
    'vertical': '|',
    'top_left': '+',
    'top_right': '+',
    'bottom_left': '+',
    'bottom_right': '+',
    'horizontal': '-',
}
CHANGED_SYNTAX_PAIRS = {
    'added': {
        None: 16,
        'comment': 17,
        'error': 18,
        'keyword': 19,
        'string': 20,
        'number': 21,
        'literal': 21,
        'function': 22,
        'class': 22,
        'decorator': 22,
        'builtin': 23,
        'constant': 23,
        'namespace': 24,
        'attribute': 24,
        'variable': 25,
        'operator': 25,
        'punctuation': 25,
        'generic': 26,
    },
    'deleted': {
        None: 27,
        'comment': 28,
        'error': 29,
        'keyword': 30,
        'string': 31,
        'number': 32,
        'literal': 32,
        'function': 33,
        'class': 33,
        'decorator': 33,
        'builtin': 34,
        'constant': 34,
        'namespace': 35,
        'attribute': 35,
        'variable': 36,
        'operator': 36,
        'punctuation': 36,
        'generic': 37,
    },
}
COMMENT_TARGET_STATES = ('open', 'superseded')
COMMENT_STATE_PRIORITY = {
    'open': 0,
    'superseded': 1,
}
CHANGED_BG_THEME_KEYS = {
    'added': 'diff_added_bg',
    'deleted': 'diff_deleted_bg',
}
COMMENT_TARGET_BG_THEME_KEYS = {
    'open': 'comment_open_bg',
    'superseded': 'comment_superseded_bg',
}
COMMENT_STATE_THEME_KEYS = {
    'open': 'comment_open_fg',
    'superseded': 'comment_superseded_fg',
    'resolved': 'resolved_fg',
    'dismissed': 'dismissed_fg',
}
CURSES_COLOR_NAMES = {
    'black': 'COLOR_BLACK',
    'red': 'COLOR_RED',
    'green': 'COLOR_GREEN',
    'yellow': 'COLOR_YELLOW',
    'blue': 'COLOR_BLUE',
    'magenta': 'COLOR_MAGENTA',
    'cyan': 'COLOR_CYAN',
    'white': 'COLOR_WHITE',
}


def run_tui(repo, state):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(render_review(repo, state), end='')
        return 0

    import curses

    app = ReviewApp(repo, state)
    curses.wrapper(app.run)
    return 0


def render_review(repo, state):
    files = review_files(repo, state)
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
    lines = []
    for row in rows_for_file(repo, state, file):
        text = plain_text_for_row(row)
        if text is not None:
            lines.append(text)
    return lines


def rows_for_file(repo, state, file):
    comments = [comment for comment in state.comments if comment.file == file.path]
    diff_lines = file_diff(repo, file).splitlines()
    if not diff_lines or file.status == 'C':
        rows = full_file_rows(repo, file)
        if not rows:
            return [RenderedLine('(no diff)', None)]
        return rows_with_comments(rows_with_comment_targets(rows, comments), comments)

    rendered = []
    seen = set()
    diff_rows = rows_with_comment_targets(full_rows_for_file(repo, file, diff_lines), comments)

    for row in diff_rows:
        for comment in matching_row_comments(comments, seen, row, 'before'):
            rendered.extend(inline_comment_rows(comment))
            seen.add(comment.id)

        rendered.append(row)

        for comment in matching_row_comments(comments, seen, row, 'after'):
            rendered.extend(inline_comment_rows(comment))
            seen.add(comment.id)

    for comment in comments:
        if comment.id not in seen:
            rendered.extend(inline_comment_rows(comment))

    return rendered


def rows_with_comments(rows, comments):
    rendered = []
    seen = set()
    for row in rows:
        for comment in matching_row_comments(comments, seen, row, 'before'):
            rendered.extend(inline_comment_rows(comment))
            seen.add(comment.id)

        rendered.append(row)

        for comment in matching_row_comments(comments, seen, row, 'after'):
            rendered.extend(inline_comment_rows(comment))
            seen.add(comment.id)

    for comment in comments:
        if comment.id not in seen:
            rendered.extend(inline_comment_rows(comment))
    return rendered


def rows_with_comment_targets(rows, comments):
    active = [comment for comment in comments if comment.state in COMMENT_TARGET_STATES]
    if not active:
        return rows
    rendered = []
    for row in rows:
        state = target_comment_state_for_row(row, active)
        rendered.append(RenderedLine(row.text, row.anchor, row.comment_id, row.kind, row.comment_state, state))
    return rendered


def target_comment_state_for_row(row, comments):
    if row.anchor is None:
        return None
    old_target = row.anchor.line if row.anchor.side == 'old' else None
    new_target = row.anchor.line if row.anchor.side == 'new' else None
    matches = [
        comment.state
        for comment in comments
        if comment_matches_line(comment, old_target, new_target)
    ]
    if not matches:
        return None
    return min(matches, key=lambda state: COMMENT_STATE_PRIORITY[state])


def review_files(repo, state):
    files = changed_files(repo)
    by_path = {file.path: file for file in files}
    for comment in state.comments:
        if comment.state not in ('open', 'superseded') or comment.file in by_path:
            continue
        by_path[comment.file] = ChangedFile(path=comment.file, status='C')
    return sorted(by_path.values(), key=lambda item: item.path)


def sidebar_rows(files):
    rows = []
    seen_folders = set()
    for file_index, file in enumerate(files):
        parts = file.path.split('/')
        for depth, name in enumerate(parts[:-1]):
            path = '/'.join(parts[:depth + 1])
            if path in seen_folders:
                continue
            seen_folders.add(path)
            rows.append(SidebarRow('folder', path, name, depth))
        rows.append(SidebarRow('file', file.path, parts[-1], len(parts) - 1, file_index))
    return rows


def file_change_counts(repo, file):
    added = 0
    removed = 0
    for line in file_diff(repo, file).splitlines():
        if line.startswith('+++') or line.startswith('---'):
            continue
        if line.startswith('+'):
            added += 1
        elif line.startswith('-'):
            removed += 1
    return added, removed


def file_comment_count(state, path):
    return len([comment for comment in state.comments if comment.file == path and comment.state in ('open', 'superseded')])


def active_comment_count(state):
    return len([comment for comment in state.comments if comment.state in ('open', 'superseded')])


def sidebar_count_segments(count, marker, attr):
    if count == 0:
        return [(' ', attr), (' ' * SIDEBAR_COUNT_WIDTH, attr)]
    return [(marker, attr), (f'{count:>{SIDEBAR_COUNT_WIDTH}}', attr)]


def ui_glyphs():
    if os.environ.get('LRV_ASCII'):
        return ASCII_GLYPHS
    return UNICODE_GLYPHS


def status_bar_text(repo, state, files, change_counts):
    added = sum(count[0] for count in change_counts.values())
    removed = sum(count[1] for count in change_counts.values())
    file_label = 'file' if len(files) == 1 else 'files'
    note_count = active_comment_count(state)
    note_label = 'note' if note_count == 1 else 'notes'
    return f'lrv review  HEAD {state.base_commit or head_revision(repo)}  {len(files)} {file_label}  +{added}  -{removed}  {note_count} {note_label}'


def refresh_superseded_comments(repo, state):
    if not any(comment.state == 'open' for comment in state.comments):
        return state

    current = current_hunks_by_file(repo)
    superseded = []
    refreshed = state
    for comment in state.comments:
        if comment.state != 'open':
            continue
        if comment.anchor_kind == 'file':
            match = refreshed_file_anchor_range(repo, comment)
            if match is None:
                superseded.append(comment.id)
            else:
                refreshed = update_comment_line_range(refreshed, comment.id, match[0], match[1])
            continue
        hunks = current.get(comment.file, {})
        current_hash = hunks.get(comment.hunk.header)
        if current_hash != comment.hunk.hash:
            superseded.append(comment.id)

    refreshed = mark_comments_superseded(refreshed, superseded)
    if refreshed is not state:
        save_state(repo, refreshed)
    return refreshed


def refreshed_file_anchor_range(repo, comment):
    lines = read_working_tree_lines(repo, comment.file)
    if lines is None or comment.file_anchor is None:
        return None
    selected = comment.file_anchor.snapshot.split('\n')
    if selected == [''] and comment.file_anchor.snapshot == '':
        selected = []
    current = lines[comment.line_range.start - 1:comment.line_range.end]
    if current == selected and hash_lines(current) == comment.file_anchor.hash:
        return (comment.line_range.start, comment.line_range.end)

    matches = exact_snapshot_matches(lines, selected)
    if len(matches) == 1:
        return matches[0]

    contextual = [match for match in matches if file_anchor_context_matches(lines, match, comment.file_anchor)]
    if len(contextual) == 1:
        return contextual[0]
    return None


def exact_snapshot_matches(lines, selected):
    if not selected:
        return []
    matches = []
    length = len(selected)
    for index in range(0, len(lines) - length + 1):
        if lines[index:index + length] == selected:
            matches.append((index + 1, index + length))
    return matches


def file_anchor_context_matches(lines, match, file_anchor):
    start, end = match
    prefix_start = max(0, start - 1 - len(file_anchor.prefix))
    prefix = lines[prefix_start:start - 1]
    suffix = lines[end:end + len(file_anchor.suffix)]
    return prefix == list(file_anchor.prefix) and suffix == list(file_anchor.suffix)


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
        and comment_matches_inline_position(comment, old_target, new_target, placement)
    ]


def comment_matches_inline_position(comment, old_line, new_line, placement):
    if comment.line_range.start != comment.line_range.end:
        return placement == 'after' and comment_matches_exact_line(comment, old_line, new_line, comment.line_range.end)
    return comment.placement == placement and comment_matches_line(comment, old_line, new_line)


def comment_matches_exact_line(comment, old_line, new_line, line):
    target = old_line if comment.side == 'old' else new_line
    return target == line


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
        anchor = anchors.get(line_number)
        if anchor is None and kind == 'unchanged':
            anchor = DiffAnchor(file.path, 'new', line_number, None)
        rendered.append(RenderedLine(f'{marker}{line_number:>4} {line}', anchor, kind=kind))
    rendered.extend(format_deleted_row(row) for row in pending_deleted)
    return rendered


def full_file_rows(repo, file):
    lines = file_lines(repo, file)
    if lines is None:
        return []
    return [
        RenderedLine(f' {line_number:>4} {line}', DiffAnchor(file.path, 'new', line_number, None), kind='unchanged')
        for line_number, line in enumerate(lines, start=1)
    ]


def format_deleted_row(row):
    text = row.text[1:] if row.text.startswith('-') else row.text
    return RenderedLine(f'-{row.anchor.line:>4} {text}', row.anchor, row.comment_id, kind='deleted', comment_state=row.comment_state)


def minimap_kind(rows):
    kinds = {row.kind for row in rows}
    if 'visual' in kinds:
        return 'visual'
    if any(row.comment_id is not None or row.text.startswith('>>>') or row.target_comment_state is not None for row in rows):
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


def read_working_tree_lines(repo, path):
    try:
        return (repo / path).read_text().splitlines()
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
        return None


def hash_lines(lines):
    snapshot = '\n'.join(lines)
    return f'sha256:{hashlib.sha256(snapshot.encode()).hexdigest()}'


def file_anchor_for_range(repo, path, start, end):
    lines = read_working_tree_lines(repo, path)
    if lines is None:
        return None
    selected = lines[start - 1:end]
    prefix_start = max(0, start - 1 - FILE_ANCHOR_CONTEXT)
    prefix = lines[prefix_start:start - 1]
    suffix = lines[end:end + FILE_ANCHOR_CONTEXT]
    return FileAnchor(
        hash=hash_lines(selected),
        snapshot='\n'.join(selected),
        prefix=prefix,
        suffix=suffix,
    )


def row_matches_anchor(row, anchor):
    if row.anchor is None:
        return False
    return row.anchor.file == anchor.file and row.anchor.side == anchor.side and row.anchor.line == anchor.line


def format_inline_comment(comment):
    lines = [f'>>> {comment.id} [{comment.state}] {comment.location()}']
    for body_line in comment.body.splitlines():
        lines.append(f'>>> {body_line}')
    return lines


def inline_comment_rows(comment):
    body = comment.body.splitlines() or ['']
    rows = [
        RenderedLine('', None, comment.id, kind='comment_top', comment_state=comment.state),
        RenderedLine(f'>>> {comment.id} [{comment.state}] {comment.location()}', None, comment.id, kind='comment_title', comment_state=comment.state),
    ]
    rows.extend(RenderedLine(f'>>> {line}', None, comment.id, kind='comment_body', comment_state=comment.state) for line in body)
    rows.append(RenderedLine('', None, comment.id, kind='comment_bottom', comment_state=comment.state))
    return rows


def plain_text_for_row(row):
    if row.kind in ('comment_top', 'comment_bottom'):
        return None
    if row.kind == 'comment_title':
        return row.text
    if row.kind == 'comment_body':
        return row.text
    return row.text


def source_row_parts(row):
    if row.comment_id is not None or row.kind not in ('unchanged', 'added', 'deleted'):
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
    def __init__(self, text, anchor, comment_id=None, kind='normal', comment_state=None, target_comment_state=None):
        self.text = text
        self.anchor = anchor
        self.comment_id = comment_id
        self.kind = kind
        self.comment_state = comment_state
        self.target_comment_state = target_comment_state


class SidebarRow:
    def __init__(self, kind, path, name, depth, file_index=None):
        self.kind = kind
        self.path = path
        self.name = name
        self.depth = depth
        self.file_index = file_index


class DiffAnchor:
    def __init__(self, file, side, line, hunk):
        self.file = file
        self.side = side
        self.line = line
        self.hunk = hunk


def anchor_kind(anchor):
    return 'hunk' if anchor.hunk is not None else 'file'


def is_commentable_anchor(anchor):
    return anchor is not None and (anchor.hunk is not None or anchor.side == 'new')


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
        self.changed_line_backgrounds = False
        self.current_line_background = False
        self.change_counts = {}
        self.pending_key = None
        self.last_reload_time = 0.0
        self.file_times = {}
        self.theme = load_theme()
        self.reload()

    def reload(self, target_anchor=None, target_scroll=None):
        selected_path = self.files[self.selected].path if self.files and self.selected < len(self.files) else None
        self.state = load_state(self.repo)
        self.state = refresh_superseded_comments(self.repo, self.state)
        self.files = review_files(self.repo, self.state)
        self.change_counts = {file.path: file_change_counts(self.repo, file) for file in self.files}
        self.file_times = {file.path: self._file_mtime(file) for file in self.files}
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

    def _file_mtime(self, file):
        try:
            return os.path.getmtime(self.repo / file.path)
        except OSError:
            return None

    def files_changed(self):
        current = {file.path: self._file_mtime(file) for file in self.files}
        if set(current.keys()) != set(self.file_times.keys()):
            return True
        return any(current[path] != self.file_times[path] for path in current)

    def run(self, screen):
        import curses

        self.set_cursor(curses, 0)
        screen.keypad(True)
        curses.halfdelay(10)  # 1/10 second blocking timeout for polling
        self.init_colors(curses)
        self.last_reload_time = time.time()

        while True:
            self.draw(screen, curses)
            key = screen.getch()
            if key == -1:
                if time.time() - self.last_reload_time >= HOT_RELOAD_INTERVAL:
                    self.last_reload_time = time.time()
                    if self.files_changed():
                        self.reload()
                continue
            if self.mode == 'input':
                self.handle_input_key(key, curses)
                continue
            if self.mode == 'comments':
                self.handle_modal_key(key, curses)
                continue
            if self.mode == 'visual':
                self.handle_visual_key(key, curses)
                continue
            if self.handle_normal_key(key, curses):
                return

    def handle_normal_key(self, key, curses):
        if self.pending_key == ord('g'):
            self.pending_key = None
            if key == ord('g'):
                self.first_diff_line()
                return False
        if key == ord('g'):
            self.pending_key = key
        elif key in (ord('q'), 27):
            return True
        elif key == ord('r'):
            self.transition_selected_inline_comment('resolved')
        elif key == ord('R'):
            self.reload()
        elif key == ord('x'):
            self.transition_selected_inline_comment('dismissed')
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
        elif key == ord('G'):
            self.last_diff_line()
        elif key == ord(']'):
            self.jump_comment(1)
        elif key == ord('['):
            self.jump_comment(-1)
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
        else:
            self.pending_key = None
        return False

    def init_colors(self, curses):
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, self.theme_color(curses, 'status_fg'), self.theme_color(curses, 'status_bg'))
        curses.init_pair(2, self.theme_color(curses, 'added_fg'), -1)
        curses.init_pair(3, self.theme_color(curses, 'deleted_fg'), -1)
        curses.init_pair(4, self.theme_color(curses, 'hunk_fg'), -1)
        curses.init_pair(5, self.theme_color(curses, 'comment_open_fg'), -1)
        curses.init_pair(6, self.theme_color(curses, 'header_fg'), self.theme_color(curses, 'header_bg'))
        curses.init_pair(7, self.theme_color(curses, 'header_active_fg'), self.theme_color(curses, 'header_active_bg'))
        curses.init_pair(8, self.theme_color(curses, 'visual_fg'), self.theme_color(curses, 'visual_bg'))
        self.init_color_pair(curses, 9, self.theme_color(curses, 'syntax_keyword_fg'), -1)
        self.init_color_pair(curses, 10, self.theme_color(curses, 'syntax_string_fg'), -1)
        self.init_color_pair(curses, 11, self.theme_color(curses, 'syntax_number_fg'), -1)
        self.init_color_pair(curses, 12, self.theme_color(curses, 'syntax_function_fg'), -1)
        self.init_color_pair(curses, 13, self.theme_color(curses, 'syntax_variable_fg'), -1)
        self.init_comment_state_color_pairs(curses)
        self.init_plain_syntax_color_pairs(curses)
        self.changed_line_backgrounds = self.changed_line_backgrounds_supported(curses)
        if self.changed_line_backgrounds:
            self.init_changed_color_pairs(curses)
            self.init_comment_target_color_pairs(curses)
        self.current_line_background = self.current_line_background_supported(curses)
        if self.current_line_background:
            self.init_current_line_color_pair(curses)
        self.syntax_colors = lex is not None

    def init_color_pair(self, curses, pair, foreground, background):
        try:
            curses.init_pair(pair, foreground, background)
        except Exception:
            pass

    def active_theme(self):
        return getattr(self, 'theme', default_theme())

    def theme_color(self, curses, key):
        custom = CUSTOM_COLOR_IDS[key]
        if self.custom_colors_supported(curses, custom):
            red, green, blue = hex_to_curses_rgb(self.active_theme()[key])
            try:
                curses.init_color(custom, red, green, blue)
                return custom
            except Exception:
                pass
        if getattr(curses, 'COLORS', 0) >= 256:
            return hex_to_xterm_256(self.active_theme()[key])
        return self.fallback_curses_color(curses, key)

    def fallback_curses_color(self, curses, key):
        name = FALLBACK_COLOR_NAMES[key]
        if name == 'magenta' and getattr(curses, 'COLORS', 0) <= 7:
            name = 'cyan'
        return getattr(curses, CURSES_COLOR_NAMES[name])

    def init_changed_color_pairs(self, curses):
        for row_kind in CHANGED_BG_PAIRS:
            background = self.changed_background_color(curses, row_kind)
            self.init_color_pair(curses, CHANGED_BG_PAIRS[row_kind], curses.COLOR_WHITE, background)
            for kind, pair in CHANGED_SYNTAX_PAIRS[row_kind].items():
                foreground = self.syntax_foreground(curses, kind)
                self.init_color_pair(curses, pair, foreground, background)

    def init_comment_target_color_pairs(self, curses):
        for state in COMMENT_TARGET_BG_PAIRS:
            background = self.comment_target_background_color(curses, state)
            self.init_color_pair(curses, COMMENT_TARGET_BG_PAIRS[state], curses.COLOR_WHITE, background)

    def init_comment_state_color_pairs(self, curses):
        for state, pair in COMMENT_STATE_FG_PAIRS.items():
            self.init_color_pair(curses, pair, self.theme_color(curses, COMMENT_STATE_THEME_KEYS[state]), -1)

    def init_plain_syntax_color_pairs(self, curses):
        initialized = set()
        for kind, pair in SYNTAX_PLAIN_PAIRS.items():
            if pair in initialized:
                continue
            initialized.add(pair)
            self.init_color_pair(curses, pair, self.syntax_foreground(curses, kind), -1)

    def init_current_line_color_pair(self, curses):
        background = self.current_line_background_color(curses)
        self.init_color_pair(curses, CURRENT_LINE_BG_PAIR, curses.COLOR_WHITE, background)

    def changed_line_backgrounds_supported(self, curses):
        return getattr(curses, 'COLORS', 0) >= 256

    def current_line_background_supported(self, curses):
        return getattr(curses, 'COLORS', 0) >= 256

    def changed_background_color(self, curses, row_kind):
        key = CHANGED_BG_THEME_KEYS[row_kind]
        custom = CUSTOM_COLOR_IDS[key]
        if self.custom_changed_backgrounds_supported(curses, custom):
            red, green, blue = hex_to_curses_rgb(self.active_theme()[key])
            try:
                curses.init_color(custom, red, green, blue)
                return custom
            except Exception:
                pass
        if getattr(curses, 'COLORS', 0) >= 256:
            return hex_to_xterm_256(self.active_theme()[key], preserve_hue=True)
        return CHANGED_BG_COLORS[row_kind]

    def comment_target_background_color(self, curses, state):
        key = COMMENT_TARGET_BG_THEME_KEYS[state]
        custom = CUSTOM_COLOR_IDS[key]
        if self.custom_changed_backgrounds_supported(curses, custom):
            red, green, blue = hex_to_curses_rgb(self.active_theme()[key])
            try:
                curses.init_color(custom, red, green, blue)
                return custom
            except Exception:
                pass
        if getattr(curses, 'COLORS', 0) >= 256:
            return hex_to_xterm_256(self.active_theme()[key], preserve_hue=True)
        return COMMENT_TARGET_BG_COLORS[state]

    def current_line_background_color(self, curses):
        custom = CUSTOM_COLOR_IDS['current_line_bg']
        if self.custom_changed_backgrounds_supported(curses, custom):
            red, green, blue = hex_to_curses_rgb(self.active_theme()['current_line_bg'])
            try:
                curses.init_color(custom, red, green, blue)
                return custom
            except Exception:
                pass
        if getattr(curses, 'COLORS', 0) >= 256:
            return hex_to_xterm_256(self.active_theme()['current_line_bg'])
        return CURRENT_LINE_BG_COLOR

    def custom_changed_backgrounds_supported(self, curses, color):
        return self.custom_colors_supported(curses, color)

    def custom_colors_supported(self, curses, color):
        try:
            can_change = curses.can_change_color()
        except Exception:
            can_change = False
        return can_change and getattr(curses, 'COLORS', 0) > color

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

    def first_diff_line(self):
        if self.focus != 'diff':
            return
        self.diff_line = 0

    def last_diff_line(self):
        if self.focus != 'diff':
            return
        lines = self.selected_file_lines()
        if not lines:
            return
        self.diff_line = len(lines) - 1

    def jump_comment(self, direction):
        if self.focus != 'diff':
            return
        rows = self.selected_file_rows()
        indexes = [index for index, row in enumerate(rows) if row.kind == 'comment_title']
        if not indexes:
            return
        if direction > 0:
            candidates = [index for index in indexes if index > self.diff_line]
        else:
            candidates = [index for index in indexes if index < self.diff_line]
        if candidates:
            self.diff_line = candidates[0] if direction > 0 else candidates[-1]

    def start_visual(self):
        if not self.files:
            return
        rows = self.selected_file_rows()
        if not rows:
            self.status_message = 'No reviewable line selected.'
            return
        anchor_index = max(0, min(len(rows) - 1, self.diff_line))
        anchor = rows[anchor_index].anchor
        if not is_commentable_anchor(anchor):
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
        for anchor in anchors:
            if anchor_kind(anchor) != anchor_kind(first):
                self.status_message = 'Range comments cannot mix hunk and file lines.'
                return None
            if first.hunk is not None and (anchor.hunk is None or anchor.hunk.header != first.hunk.header or anchor.hunk.hash != first.hunk.hash):
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
            rendered.append(RenderedLine(row.text, row.anchor, row.comment_id, kind, row.comment_state, row.target_comment_state))
        return rendered

    def diff_progress_label(self):
        rows = self.selected_file_rows()
        if not rows:
            return '0%'
        if self.diff_line <= 0:
            return 'Top'
        if self.diff_line >= len(rows) - 1:
            return 'Bot'
        return f'{round((self.diff_line + 1) * 100 / len(rows))}%'

    def selected_file_lines(self):
        lines = []
        for row in self.selected_file_rows():
            text = plain_text_for_row(row)
            if text is not None:
                lines.append(text)
        return lines

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
        elif key == ord('d'):
            self.delete_selected_modal_comment()

    def transition_selected_modal_comment(self, comment_state):
        comment = self.selected_modal_comment()
        if comment is None:
            return
        original_state = self.modal_original_states.get(comment.id, comment.state)
        next_state = original_state if comment.state == comment_state else comment_state
        self.state = set_comment_state(self.state, comment.id, next_state)
        save_state(self.repo, self.state)

    def transition_selected_inline_comment(self, comment_state):
        comment = self.selected_inline_comment()
        if comment is None:
            return False
        self.state = set_comment_state(self.state, comment.id, comment_state)
        save_state(self.repo, self.state)
        return True

    def delete_selected_modal_comment(self):
        comment = self.selected_modal_comment()
        if comment is None:
            return
        self.state = remove_comment(self.state, comment.id)
        save_state(self.repo, self.state)
        self.modal_comment_ids = tuple(id for id in self.modal_comment_ids if id != comment.id)
        self.modal_original_states.pop(comment.id, None)
        comments = self.modal_comments()
        self.modal_index = max(0, min(len(comments) - 1, self.modal_index))

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
        if not is_commentable_anchor(self.input_anchor):
            return
        self.input_text = ''
        self.input_end_line = None
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
        comment_id = None
        for i in range(index, -1, -1):
            if rows[i].comment_id is not None:
                comment_id = rows[i].comment_id
                break
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
            anchor_kind_value = anchor_kind(anchor)
            file_anchor = None
            if anchor_kind_value == 'file':
                end_line = self.input_end_line if self.input_end_line is not None else anchor.line
                file_anchor = file_anchor_for_range(self.repo, anchor.file, min(anchor.line, end_line), max(anchor.line, end_line))
                if file_anchor is None:
                    self.status_message = 'Could not read file for comment anchor.'
                    self.cancel_input(curses)
                    return
            self.state = append_comment(
                state,
                anchor.file,
                anchor.side,
                anchor.line,
                anchor.hunk,
                body,
                placement=self.saved_input_placement(anchor),
                end_line=self.input_end_line,
                anchor_kind=anchor_kind_value,
                file_anchor=file_anchor,
            )
            save_state(self.repo, self.state)
            self.cancel_input(curses)
            self.reload(target_anchor=anchor, target_scroll=scroll)
            return
        self.cancel_input(curses)

    def saved_input_placement(self, anchor):
        end_line = self.input_end_line if self.input_end_line is not None else anchor.line
        if end_line != anchor.line:
            return 'after'
        return self.input_placement

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
        self.draw_vertical_border(screen, 1, sidebar_width, max(0, height - 1), curses)
        self.draw_sidebar(screen, height, sidebar_width, curses)
        self.draw_main(screen, height, width, main_x, curses)
        self.draw_input(screen, height, width, curses)
        self.draw_comment_modal(screen, height, width, curses)
        screen.refresh()

    def draw_header(self, screen, width, curses):
        title = status_bar_text(self.repo, self.state, self.files, self.change_counts)
        attr = curses.color_pair(7) if self.mode in ('input', 'visual') else curses.color_pair(6)
        if attr == 0:
            attr = curses.A_REVERSE
        self.addstr(screen, 0, 0, self.centered_text(title, width - 1), attr)

    def centered_text(self, text, width):
        if width <= 0:
            return ''
        if len(text) >= width:
            return text[:width]
        left = (width - len(text)) // 2
        right = width - len(text) - left
        return ' ' * left + text + ' ' * right

    def draw_vertical_border(self, screen, y, x, height, curses):
        glyph = ui_glyphs()['vertical']
        for offset in range(height):
            self.addstr(screen, y + offset, x, glyph, curses.A_DIM)

    def draw_sidebar(self, screen, height, width, curses):
        attr = curses.A_BOLD
        self.addstr(screen, 2, 1, 'Review outline', attr)
        if not self.files:
            self.addstr(screen, 4, 1, 'none')
            return

        rows = sidebar_rows(self.files)
        for index, row in enumerate(rows[:height - 4]):
            if row.kind == 'folder':
                self.draw_sidebar_folder(screen, index + 4, 1, width - 2, row, curses)
                continue
            file = self.files[row.file_index]
            added, removed = self.change_counts.get(file.path, (0, 0))
            comments = file_comment_count(self.state, file.path)
            selected = row.file_index == self.selected
            attr = self.current_line_attr(curses) if selected and self.focus == 'files' else curses.A_NORMAL
            if selected and self.focus == 'diff':
                attr = curses.A_DIM
            self.draw_sidebar_file(screen, index + 4, 1, width - 2, row, file, added, removed, comments, attr, curses)

    def draw_sidebar_folder(self, screen, y, x, width, row, curses):
        indent = '  ' * row.depth
        label = f'{indent}{row.name}/'
        self.addstr(screen, y, x, label[:width], curses.A_BOLD | curses.A_DIM)

    def draw_sidebar_file(self, screen, y, x, width, row, file, added, removed, comments, attr, curses):
        if attr != curses.A_NORMAL:
            self.addstr(screen, y, x, ' ' * max(0, width), attr)
        segments = []
        segments.extend(sidebar_count_segments(added, '+', curses.color_pair(2) | curses.A_BOLD))
        segments.append((' ', curses.A_NORMAL))
        segments.extend(sidebar_count_segments(removed, '-', curses.color_pair(3) | curses.A_BOLD))
        segments.append((' ', curses.A_NORMAL))
        segments.extend(sidebar_count_segments(comments, '*', curses.color_pair(11) | curses.A_BOLD))
        stats_width = sum(len(text) for text, _attr in segments)
        label_width = min(SIDEBAR_FILE_LABEL_WIDTH, max(0, width - stats_width - 1))
        indent = '  ' * row.depth
        prefix = f'{indent}{file.status} '
        prefix = prefix[:label_width]
        row_attr = attr if attr != curses.A_NORMAL else curses.A_NORMAL
        self.addstr(screen, y, x, prefix, row_attr)
        name_width = max(0, label_width - len(prefix))
        name = row.name
        if len(name) > name_width:
            name = name[:max(0, name_width - 1)] + '~' if name_width > 0 else ''
        self.addstr(screen, y, x + len(prefix), name[:name_width], attr)
        column = x + label_width
        if label_width > 0:
            gap = max(1, width - label_width - stats_width)
            self.addstr(screen, y, column, ' ' * gap, row_attr)
            column += gap
        for text, segment_attr in segments:
            if column - x >= width:
                break
            value = text[:max(0, width - (column - x))]
            if attr != curses.A_NORMAL:
                if attr == curses.color_pair(CURRENT_LINE_BG_PAIR):
                    segment_attr = attr
                else:
                    segment_attr |= attr
            self.addstr(screen, y, column, value, segment_attr)
            column += len(value)

    def draw_main(self, screen, height, width, x, curses):
        if not self.files:
            self.addstr(screen, 2, x, 'No changes in working tree.')
            return

        file = self.files[self.selected]
        title = file.path
        attr = curses.A_BOLD
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
            if row.kind.startswith('comment_'):
                self.draw_comment_box_row(screen, index + 4, x, row, text_width, attr, selected, curses)
                continue
            if self.draw_source_row(screen, index + 4, x, row, file.path, text_width, attr, selected, curses):
                continue
            if self.focus == 'diff' and self.scroll + index == self.diff_line:
                attr = self.selected_line_attr(curses, attr)
            value = row.text[:text_width]
            if selected:
                value = value.ljust(max(0, text_width))
            self.addstr(screen, index + 4, x, value, attr)

        if minimap_x is not None:
            self.draw_minimap(screen, display_rows, 4, minimap_x, visible_height, curses)

        footer = self.status_message or self.diff_progress_label()
        self.addstr(screen, height - 1, x, footer[:max(0, width - x - 1)], curses.A_BOLD)

    def draw_comment_box_row(self, screen, y, x, row, width, attr, selected, curses):
        if width <= 0:
            return
        if selected:
            attr = self.selected_line_attr(curses, attr)
        glyphs = ui_glyphs()
        if row.kind == 'comment_top':
            value = glyphs['top_left'] + glyphs['horizontal'] * max(0, width - 2) + glyphs['top_right']
        elif row.kind == 'comment_bottom':
            value = glyphs['bottom_left'] + glyphs['horizontal'] * max(0, width - 2) + glyphs['bottom_right']
        else:
            text = row.text[4:] if row.text.startswith('>>> ') else row.text
            content = f' {text} '
            if row.kind == 'comment_title':
                content = f' Note - {text} '
            inner_width = max(0, width - 2)
            value = glyphs['vertical'] + content[:inner_width].ljust(inner_width) + glyphs['vertical']
        self.addstr(screen, y, x, value[:width], attr)

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

        title = 'Comments  enter jump  r resolve  x dismiss  d delete  q close'
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
        return self.comment_state_attr(curses, comment.state)

    def comment_state_attr(self, curses, state):
        if state == 'open':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['open']) | curses.A_BOLD
        if state == 'superseded':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['superseded'])
        if state == 'resolved':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['resolved'])
        if state == 'dismissed':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['dismissed'])
        return curses.A_NORMAL

    def comment_target_state_attr(self, curses, state):
        if getattr(self, 'changed_line_backgrounds', False):
            return curses.color_pair(COMMENT_TARGET_BG_PAIRS[state])
        if state == 'open':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['open'])
        if state == 'superseded':
            return curses.color_pair(COMMENT_STATE_FG_PAIRS['superseded'])
        return curses.A_NORMAL

    def line_attr(self, curses, row):
        line = row.text
        if row.comment_id is not None or row.kind.startswith('comment_'):
            return self.comment_state_attr(curses, row.comment_state or 'open')
        if row.kind == 'visual':
            return curses.color_pair(8) | curses.A_BOLD
        if row.target_comment_state is not None:
            return self.comment_target_state_attr(curses, row.target_comment_state)
        if row.kind == 'added':
            if self.changed_line_backgrounds:
                return curses.color_pair(CHANGED_BG_PAIRS['added'])
            return curses.color_pair(2)
        if row.kind == 'deleted':
            if self.changed_line_backgrounds:
                return curses.color_pair(CHANGED_BG_PAIRS['deleted'])
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
        if getattr(self, 'current_line_background', False):
            return curses.color_pair(CURRENT_LINE_BG_PAIR)
        return curses.A_DIM

    def selected_line_attr(self, curses, base_attr):
        if getattr(self, 'current_line_background', False):
            return self.current_line_attr(curses)
        return base_attr | self.current_line_attr(curses)

    def draw_source_row(self, screen, y, x, row, path, width, base_attr, selected, curses):
        if not getattr(self, 'syntax_colors', False):
            return False
        parts = source_row_parts(row)
        if parts is None or width <= 0:
            return False
        prefix, code = parts
        attr = self.selected_line_attr(curses, base_attr) if selected else base_attr
        if row.target_comment_state is not None:
            self.addstr(screen, y, x, row.text[:width].ljust(width), attr)
            return True
        if selected:
            self.addstr(screen, y, x, ' ' * width, attr)
        self.addstr(screen, y, x, prefix[:width], attr)
        column = x + min(len(prefix), width)
        remaining = width - min(len(prefix), width)
        if remaining <= 0:
            return True
        for text, kind in syntax_spans(path, code):
            if remaining <= 0:
                break
            value = text.replace('\n', '').replace('\r', '')[:remaining]
            if not value:
                continue
            span_attr = self.syntax_attr(curses, kind, base_attr)
            if selected:
                span_attr = self.selected_line_attr(curses, span_attr)
            self.addstr(screen, y, column, value, span_attr)
            column += len(value)
            remaining -= len(value)
        if self.changed_row_kind_for_attr(curses, base_attr) is not None and remaining > 0:
            self.addstr(screen, y, column, ' ' * remaining, attr)
        return True

    def syntax_attr(self, curses, kind, base_attr):
        row_kind = self.changed_row_kind_for_attr(curses, base_attr)
        if row_kind is not None:
            attr = curses.color_pair(CHANGED_SYNTAX_PAIRS[row_kind].get(kind, CHANGED_SYNTAX_PAIRS[row_kind][None]))
            if kind in ('error', 'keyword', 'function', 'class', 'decorator', 'builtin', 'constant', 'operator', 'punctuation'):
                attr |= curses.A_BOLD
            if kind == 'comment':
                attr |= curses.A_DIM
            return attr
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
            return curses.color_pair(SYNTAX_PLAIN_PAIRS['builtin']) | curses.A_BOLD
        if kind in ('namespace', 'attribute'):
            return curses.color_pair(9)
        if kind == 'variable':
            return curses.color_pair(13)
        if kind in ('operator', 'punctuation'):
            return curses.color_pair(SYNTAX_PLAIN_PAIRS['operator']) | curses.A_BOLD
        if kind == 'generic':
            return curses.color_pair(SYNTAX_PLAIN_PAIRS['generic'])
        return base_attr

    def changed_row_kind_for_attr(self, curses, attr):
        if not getattr(self, 'changed_line_backgrounds', True):
            return None
        if attr == curses.color_pair(CHANGED_BG_PAIRS['added']):
            return 'added'
        if attr == curses.color_pair(CHANGED_BG_PAIRS['deleted']):
            return 'deleted'
        return None

    def syntax_foreground(self, curses, kind):
        if kind == 'comment':
            return self.theme_color(curses, 'syntax_comment_fg')
        if kind == 'error':
            return self.theme_color(curses, 'syntax_error_fg')
        if kind == 'keyword':
            return self.theme_color(curses, 'syntax_keyword_fg')
        if kind == 'string':
            return self.theme_color(curses, 'syntax_string_fg')
        if kind in ('number', 'literal'):
            return self.theme_color(curses, 'syntax_number_fg')
        if kind in ('builtin', 'constant'):
            return self.theme_color(curses, 'syntax_builtin_fg')
        if kind in ('function', 'class', 'decorator'):
            return self.theme_color(curses, 'syntax_function_fg')
        if kind in ('namespace', 'attribute', 'generic'):
            if kind == 'generic':
                return self.theme_color(curses, 'syntax_generic_fg')
            return self.theme_color(curses, 'syntax_namespace_fg')
        if kind in ('variable', 'operator', 'punctuation'):
            if kind in ('operator', 'punctuation'):
                return self.theme_color(curses, 'syntax_operator_fg')
            return self.theme_color(curses, 'syntax_variable_fg')
        return self.theme_color(curses, 'syntax_variable_fg')

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

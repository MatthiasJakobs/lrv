import json
from datetime import datetime, timezone


class StateError(RuntimeError):
    pass


class LineRange:
    def __init__(self, start, end):
        self.start = start
        self.end = end

    def label(self):
        if self.start == self.end:
            return str(self.start)
        return f'{self.start}-{self.end}'


class HunkSnapshot:
    def __init__(self, header, hash, snapshot):
        self.header = header
        self.hash = hash
        self.snapshot = snapshot


class Comment:
    def __init__(self, id, state, file, side, line_range, hunk, body, created_at, updated_at, placement='after'):
        self.id = id
        self.state = state
        self.file = file
        self.side = side
        self.line_range = line_range
        self.hunk = hunk
        self.body = body
        self.created_at = created_at
        self.updated_at = updated_at
        self.placement = placement

    def location(self):
        return f'{self.file}:{self.line_range.label()}'


class ReviewState:
    def __init__(self, version, repo_root, base_commit, comments):
        self.version = version
        self.repo_root = repo_root
        self.base_commit = base_commit
        self.comments = tuple(comments)


def state_path(repo):
    return repo / '.git' / 'lpr' / 'state.json'


def load_state(repo):
    path = state_path(repo)
    if not path.exists():
        return ReviewState(version=1, repo_root=str(repo), base_commit='', comments=())

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise StateError(f'Invalid LPR state JSON: {path}: {error}') from error

    return parse_state(data)


def save_state(repo, state):
    path = state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state_to_data(state), indent=2) + '\n')


def state_to_data(state):
    return {
        'version': state.version,
        'repo': {
            'root': state.repo_root,
            'baseCommit': state.base_commit,
        },
        'comments': [comment_to_data(comment) for comment in state.comments],
    }


def comment_to_data(comment):
    return {
        'id': comment.id,
        'state': comment.state,
        'file': comment.file,
        'side': comment.side,
        'lineRange': {
            'start': comment.line_range.start,
            'end': comment.line_range.end,
        },
        'hunk': {
            'header': comment.hunk.header,
            'hash': comment.hunk.hash,
            'snapshot': comment.hunk.snapshot,
        },
        'body': comment.body,
        'createdAt': comment.created_at,
        'updatedAt': comment.updated_at,
        'placement': comment.placement,
    }


def append_comment(state, file, side, line, hunk, body, placement='after'):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    comment = Comment(
        id=next_comment_id(state.comments),
        state='open',
        file=file,
        side=side,
        line_range=LineRange(start=line, end=line),
        hunk=hunk,
        body=body,
        created_at=now,
        updated_at=now,
        placement=placement,
    )
    return ReviewState(
        version=state.version,
        repo_root=state.repo_root,
        base_commit=state.base_commit,
        comments=(*state.comments, comment),
    )


def remove_comment(state, comment_id):
    return ReviewState(
        version=state.version,
        repo_root=state.repo_root,
        base_commit=state.base_commit,
        comments=tuple(comment for comment in state.comments if comment.id != comment_id),
    )


def next_comment_id(comments):
    highest = 0
    for comment in comments:
        if not comment.id.startswith('LPR-'):
            continue
        try:
            highest = max(highest, int(comment.id[4:]))
        except ValueError:
            pass
    return f'LPR-{highest + 1:03d}'


def parse_state(data):
    repo = data.get('repo', {})
    comments = tuple(parse_comment(comment) for comment in data.get('comments', []))
    return ReviewState(
        version=int(data.get('version', 1)),
        repo_root=str(repo.get('root', '')),
        base_commit=str(repo.get('baseCommit', '')),
        comments=comments,
    )


def parse_comment(data):
    line_range = data.get('lineRange', {})
    hunk = data.get('hunk', {})
    return Comment(
        id=str(data['id']),
        state=str(data['state']),
        file=str(data['file']),
        side=str(data['side']),
        line_range=LineRange(start=int(line_range['start']), end=int(line_range['end'])),
        hunk=HunkSnapshot(header=str(hunk['header']), hash=str(hunk['hash']), snapshot=str(hunk['snapshot'])),
        body=str(data['body']),
        created_at=str(data['createdAt']),
        updated_at=str(data['updatedAt']),
        placement=str(data.get('placement', 'after')),
    )


def comments_by_state(state):
    grouped = {}
    for comment in state.comments:
        grouped.setdefault(comment.state, []).append(comment)
    for comments in grouped.values():
        comments.sort(key=lambda comment: comment.id)
    return grouped

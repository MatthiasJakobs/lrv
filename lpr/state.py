import json


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
    def __init__(self, id, state, file, side, line_range, hunk, body, created_at, updated_at):
        self.id = id
        self.state = state
        self.file = file
        self.side = side
        self.line_range = line_range
        self.hunk = hunk
        self.body = body
        self.created_at = created_at
        self.updated_at = updated_at

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
    )


def comments_by_state(state):
    grouped = {}
    for comment in state.comments:
        grouped.setdefault(comment.state, []).append(comment)
    for comments in grouped.values():
        comments.sort(key=lambda comment: comment.id)
    return grouped

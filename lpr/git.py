import difflib
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class ChangedFile:
    def __init__(self, path, status):
        self.path = path
        self.status = status


def run_git(cwd, *args):
    result = subprocess.run(['git', *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        command = ' '.join(args)
        raise GitError(message or f'git {command} failed')
    return result.stdout.strip()


def repo_root(cwd):
    return Path(run_git(cwd, 'rev-parse', '--show-toplevel'))


def head_revision(repo):
    return run_git(repo, 'rev-parse', '--short', 'HEAD')


def changed_files(repo):
    tracked = run_git(repo, 'diff', '--name-status').splitlines()
    files = [parse_name_status(line) for line in tracked if line]

    untracked = run_git(repo, 'ls-files', '--others', '--exclude-standard').splitlines()
    files.extend(ChangedFile(path=path, status='??') for path in untracked if path)

    return sorted(files, key=lambda item: item.path)


def file_diff(repo, file):
    if file.status == '??':
        return untracked_file_diff(repo, file.path)
    return run_git(repo, 'diff', '--', file.path)


def file_lines(repo, file):
    full_path = repo / file.path
    try:
        return full_path.read_text().splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return None


def untracked_file_diff(repo, path):
    full_path = repo / path
    try:
        lines = full_path.read_text().splitlines()
    except UnicodeDecodeError:
        return f'diff --git a/{path} b/{path}\nnew file mode 100644\nBinary file {path} differs'

    diff = difflib.unified_diff([], lines, fromfile='/dev/null', tofile=f'b/{path}', lineterm='')
    return '\n'.join(diff)


def parse_name_status(line):
    parts = line.split('\t')
    status = parts[0]
    path = parts[-1]
    return ChangedFile(path=path, status=status)

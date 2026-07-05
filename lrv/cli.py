import argparse
import sys
from pathlib import Path

from lrv.export import render_export, render_single_comment
from lrv.git import GitError, changed_files, head_revision, repo_root
from lrv.state import StateError, comments_by_state, load_state
from lrv.tui import refresh_superseded_comments, run_tui


def main(argv=None):
    normalized_argv, leading_repo = normalize_argv(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(normalized_argv)
    args.leading_repo = leading_repo

    try:
        repo = repo_root(command_path(args))
        state = refresh_superseded_comments(repo, load_state(repo))
        return args.handler(repo, state, args)
    except (GitError, StateError) as error:
        print(f'lrv: {error}', file=sys.stderr)
        return 1


def build_parser():
    parser = argparse.ArgumentParser(prog='lrv')
    parser.add_argument('-C', '--repo', type=Path, help='Repository path. Defaults to the current directory.')
    parser.set_defaults(handler=command_tui)
    subcommands = parser.add_subparsers(dest='command')

    status = subcommands.add_parser('status', help='Show changed files and review comment counts.')
    status.add_argument('path', nargs='?', type=Path, help='Repository path. Defaults to the current directory.')
    status.set_defaults(handler=command_status)

    export = subcommands.add_parser('export', help='Export open comments as deterministic Markdown.')
    export.add_argument('path', nargs='?', type=Path, help='Repository path. Defaults to the current directory.')
    export.set_defaults(handler=command_export)

    show = subcommands.add_parser('show', help='Show one comment as Markdown.')
    show.add_argument('id', help='Comment ID, for example LRV-001.')
    show.add_argument('path', nargs='?', type=Path, help='Repository path. Defaults to the current directory.')
    show.set_defaults(handler=command_show)

    return parser


def normalize_argv(argv):
    commands = {'status', 'export', 'show'}
    if not argv or argv[0].startswith('-') or argv[0] in commands:
        return argv, None
    return argv[1:], Path(argv[0])


def command_path(args):
    return args.repo or args.leading_repo or getattr(args, 'path', None) or Path.cwd()


def command_status(repo, state, args):
    del args
    grouped = comments_by_state(state)
    print(f'Base: HEAD {state.base_commit or head_revision(repo)}')
    print('')
    print('Changed files:')
    files = changed_files(repo)
    if files:
        for file in files:
            print(f'  {file.status} {file.path}')
    else:
        print('  none')

    print('')
    print('Comments:')
    for name in ('open', 'superseded', 'resolved', 'dismissed'):
        comments = grouped.get(name, [])
        ids = ', '.join(comment.id for comment in comments) or '-'
        print(f'  {name}: {len(comments)} ({ids})')
    return 0


def command_tui(repo, state, args):
    del args
    return run_tui(repo, state)


def command_export(repo, state, args):
    del repo, args
    print(render_export(state), end='')
    return 0


def command_show(repo, state, args):
    del repo
    comment = find_comment(state.comments, args.id)
    if comment is None:
        print(f'lrv: unknown comment id: {args.id}', file=sys.stderr)
        return 1
    print(render_single_comment(state, comment), end='')
    return 0


def find_comment(comments, comment_id):
    for comment in comments:
        if comment.id == comment_id:
            return comment
    return None

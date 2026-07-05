#!/usr/bin/env python3
'''Create a throwaway Git repository from an lrv fixture.'''

import argparse
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / 'fixtures' / 'repos'


def run_git(repo, *args):
    result = subprocess.run(['git', *args], cwd=repo, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def copy_tree_contents(source, destination):
    if not source.exists():
        return

    for item in source.rglob('*'):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def write_review_state(repo, fixture, head):
    state_template = fixture / 'git_state' / 'lrv' / 'state.json'
    if not state_template.exists():
        return

    state = json.loads(state_template.read_text())
    if state.get('repo', {}).get('baseCommit') == '__HEAD__':
        state['repo']['baseCommit'] = head
    if state.get('repo', {}).get('root') == '__REPO_ROOT__':
        state['repo']['root'] = str(repo)

    state_dir = repo / '.git' / 'lrv'
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / 'state.json').write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')


def materialize(fixture_name, destination):
    fixture = FIXTURE_ROOT / fixture_name
    if not fixture.exists():
        raise SystemExit(f'Unknown fixture: {fixture_name}')

    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(f'Destination must be empty: {destination}')

    destination.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(fixture / 'base', destination)

    run_git(destination, 'init')
    run_git(destination, 'config', 'user.name', 'LRV Fixture')
    run_git(destination, 'config', 'user.email', 'fixture@example.invalid')
    run_git(destination, 'add', '.')
    run_git(destination, 'commit', '-m', 'Fixture base')
    head = run_git(destination, 'rev-parse', 'HEAD')

    copy_tree_contents(fixture / 'tracked_changes', destination)
    copy_tree_contents(fixture / 'untracked', destination)
    write_review_state(destination, fixture, head)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', help='Fixture name under fixtures/repos')
    parser.add_argument('destination', type=Path, help='Empty destination directory')
    return parser.parse_args()


def main():
    args = parse_args()
    materialize(args.fixture, args.destination)


if __name__ == '__main__':
    main()

# python-review-basic

Fixture for the v1 review loop.

The `base` directory is committed as `HEAD`. The `tracked_changes` directory is
then copied over the checkout, and `untracked` files are copied after that.

This gives tests and local development a real Git repository with:

- several modified tracked Python files
- added, deleted, and context lines in larger unified diffs
- multiple changed hunks in tracked files
- multiple untracked Python files that v1 should represent as all-added
- a mock `.git/lrv/state.json` review state with open, superseded, and dismissed comments

Create a temporary repo with:

```sh
python scripts/materialize_fixture_repo.py python-review-basic /tmp/lrv-fixture
```

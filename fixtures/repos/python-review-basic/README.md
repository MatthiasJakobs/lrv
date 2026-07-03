# python-review-basic

Fixture for the v1 review loop.

The `base` directory is committed as `HEAD`. The `tracked_changes` directory is
then copied over the checkout, and `untracked` files are copied after that.

This gives tests and local development a real Git repository with:

- modified tracked Python files
- added and deleted lines in a unified diff
- multiple hunks in one file
- an untracked Python file that v1 should represent as all-added
- a mock `.git/lpr/state.json` review state

Create a temporary repo with:

```sh
python scripts/materialize_fixture_repo.py python-review-basic /tmp/lpr-fixture
```

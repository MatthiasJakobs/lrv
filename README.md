# lrv

`lrv` is a local review tool for coding-agent changes. It lets a human review the current Git working tree, attach comments to diff lines, and expose those comments through a CLI so an agent can address them without creating a remote branch or GitHub pull request.

## Commands

```sh
lrv
lrv status
lrv export
lrv show <id>
```

`lrv` opens the review TUI. `lrv status` summarizes changed files and comment states. `lrv export` prints deterministic Markdown for all open comments, which is the main agent handoff. `lrv show <id>` prints one comment.

Human maintenance commands are reserved for the reviewer:

```sh
lrv resolve <id>
lrv dismiss <id>
lrv clear --superseded
```

Agents should not call human maintenance commands or edit `.git/lrv/state.json` directly.

## Install

From this checkout:

```sh
pipx install -e .
```

To enable syntax highlighting in the TUI, install the optional highlighting extra:

```sh
pipx install -e '.[highlight]'
```

For an existing `pipx` install, inject the optional dependency:

```sh
pipx inject lrv pygments
```

Without `pygments`, the TUI falls back to the plain black-and-white/diff-color rendering.

On macOS, `pipx` avoids installing into the externally managed system or Homebrew Python environment. If `pipx` is not installed, use `brew install pipx` and then `pipx ensurepath`.

## Theme

`lrv` loads an optional user theme from:

```text
${XDG_CONFIG_HOME}/lrv/theme.yaml
```

If `XDG_CONFIG_HOME` is not set, it falls back to:

```text
~/.config/lrv/theme.yaml
```

Theme values are flat semantic keys with HTML hex colors:

```yaml
added_fg: '#00aa00'
deleted_fg: '#aa0000'
hunk_fg: '#00aaaa'
comment_open_fg: '#aaaa00'
comment_superseded_fg: '#00aaaa'
resolved_fg: '#00aa00'
dismissed_fg: '#aa0000'
visual_fg: '#000000'
visual_bg: '#00aaaa'
status_fg: '#000000'
status_bg: '#ffffff'
status_active_fg: '#000000'
status_active_bg: '#aaaa00'
header_fg: '#000000'
header_bg: '#ffffff'
header_active_fg: '#000000'
header_active_bg: '#aaaa00'
diff_added_bg: '#050e09'
diff_deleted_bg: '#120706'
comment_open_bg: '#121004'
comment_superseded_bg: '#040d11'
current_line_bg: '#090909'
syntax_keyword_fg: '#00aaaa'
syntax_string_fg: '#aaaa00'
syntax_number_fg: '#aa00aa'
syntax_function_fg: '#0000aa'
syntax_builtin_fg: '#aa00aa'
syntax_namespace_fg: '#00aaaa'
syntax_variable_fg: '#ffffff'
syntax_operator_fg: '#ffffff'
syntax_generic_fg: '#00aaaa'
syntax_error_fg: '#aa0000'
syntax_comment_fg: '#ffffff'
```

Missing files, malformed YAML, unknown keys, and invalid color values are ignored. Valid keys are merged over the built-in theme.

To install the bundled agent skill, copy the skill directory to your agent's user skills directory:

```sh
mkdir -p ~/.agents/skills
cp -R skills/lrv-review ~/.agents/skills/
```

Codex reads user skills from `~/.agents/skills`, so the command above installs `$lrv-review` for Codex.

For Claude and Pi coding agents, install the same `skills/lrv-review` directory wherever that agent loads user skills. If the agent follows the open Agent Skills layout, use `~/.agents/skills/lrv-review`. If it uses an import UI, import or upload the `skills/lrv-review` folder.

## Agent-Human Loop

1. The agent changes code in the working tree.
2. The human opens `lrv`, reviews the diff, and adds comments.
3. The agent runs `lrv status`, then `lrv export`.
4. The agent edits the working tree to address open comments.
5. `lrv` marks affected comments as superseded when reviewed hunks change.
6. The human re-reviews the updated diff and decides what is resolved.

The optional bundled skill in `skills/lrv-review/` teaches compatible agents this workflow.

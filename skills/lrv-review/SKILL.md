---
name: lrv-review
description: Address local review comments from lrv when the user asks an agent to handle LRV review feedback.
---

Use this skill only when the user explicitly asks you to address, inspect, or work through LRV review comments.

`lrv` is a local review tool. A human reviewer writes comments in local review state, and the agent reads only the information needed to update the working tree.

## Workflow

1. Run `lrv status` in the repository.
2. Run `lrv export` to read the open comments.
3. If the export says there are no open comments, stop without making code changes and report that there are no open LRV comments.
4. Address the open comments by editing the relevant code, tests, or documentation.
5. Run the narrowest relevant tests or checks you can discover from the repository. Prefer existing project commands.
6. Run `lrv status` again.
7. Repeat `lrv status`, `lrv export`, edits, and validation while open comments remain.

Stop the loop if a pass leaves the same open comment IDs unchanged. Explain that progress is blocked and report the remaining IDs.

## Commands

Use `lrv export` as the source of truth for all open comments. It prints all currently open comments with the reviewed diff context.

Use `lrv show <id>` only when you need to inspect one specific comment again.

## Boundaries

Do not edit `.git/lrv/state.json` directly.

Do not run human maintenance commands such as:

```sh
lrv resolve <id>
lrv dismiss <id>
lrv clear --superseded
```

Do not mark comments resolved, dismissed, or cleared. The human reviewer owns review state. Your job is to change the working tree so `lrv` can supersede affected comments naturally, then report what changed and what validation ran.

If no relevant validation command is discoverable, say that explicitly.

def render_export(state, comments=None):
    selected = comments if comments is not None else open_comments(state)
    lines = [
        '# LRV Review',
        '',
        f'Base: HEAD {state.base_commit}',
        '',
        '## Open Comments',
        '',
    ]

    if not selected:
        lines.append('No open comments.')
        return '\n'.join(lines) + '\n'

    for index, comment in enumerate(selected):
        if index:
            lines.append('')
        lines.extend(render_comment(comment))

    lines.extend(
        [
            '',
            'Instruction:',
            'Address the comment above in the code. Do not resolve, dismiss, or clear LRV comments.',
        ]
    )
    return '\n'.join(lines) + '\n'


def render_single_comment(state, comment):
    lines = [
        '# LRV Comment',
        '',
        f'Base: HEAD {state.base_commit}',
        '',
    ]
    lines.extend(render_comment(comment, include_state=True))
    return '\n'.join(lines) + '\n'


def render_comment(comment, include_state=False):
    title = f'### {comment.id} {comment.location()}'
    lines = [title, '']
    if include_state:
        lines.extend(['State:', comment.state, ''])
    if comment.anchor_kind == 'file':
        context_label = 'Reviewed file context:'
        context = comment.file_anchor.snapshot
    else:
        context_label = 'Reviewed diff context:'
        context = comment.hunk.snapshot
    lines.extend(
        [
            'Comment:',
            comment.body,
            '',
            context_label,
            '',
            '```diff',
            context,
            '```',
        ]
    )
    return lines


def open_comments(state):
    return sorted([comment for comment in state.comments if comment.state == 'open'], key=lambda comment: comment.id)

def render_export(state, comments=None):
    selected = comments if comments is not None else open_comments(state)
    lines = [
        '# LPR Review',
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
            'Address the comment above in the code. Do not resolve, dismiss, or clear LPR comments.',
        ]
    )
    return '\n'.join(lines) + '\n'


def render_single_comment(state, comment):
    lines = [
        '# LPR Comment',
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
    lines.extend(
        [
            'Comment:',
            comment.body,
            '',
            'Reviewed diff context:',
            '',
            '```diff',
            comment.hunk.snapshot,
            '```',
        ]
    )
    return lines


def open_comments(state):
    return sorted([comment for comment in state.comments if comment.state == 'open'], key=lambda comment: comment.id)

import os
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None


DEFAULT_THEME = {
    'added_fg': '#00aa00',
    'deleted_fg': '#aa0000',
    'hunk_fg': '#00aaaa',
    'comment_open_fg': '#aaaa00',
    'comment_superseded_fg': '#00aaaa',
    'resolved_fg': '#00aa00',
    'dismissed_fg': '#aa0000',
    'visual_fg': '#000000',
    'visual_bg': '#00aaaa',
    'status_fg': '#000000',
    'status_bg': '#ffffff',
    'status_active_fg': '#000000',
    'status_active_bg': '#aaaa00',
    'header_fg': '#000000',
    'header_bg': '#ffffff',
    'header_active_fg': '#000000',
    'header_active_bg': '#aaaa00',
    'diff_added_bg': '#050e09',
    'diff_deleted_bg': '#120706',
    'comment_open_bg': '#121004',
    'comment_superseded_bg': '#040d11',
    'current_line_bg': '#090909',
    'syntax_keyword_fg': '#00aaaa',
    'syntax_string_fg': '#aaaa00',
    'syntax_number_fg': '#aa00aa',
    'syntax_function_fg': '#0000aa',
    'syntax_builtin_fg': '#aa00aa',
    'syntax_namespace_fg': '#00aaaa',
    'syntax_variable_fg': '#ffffff',
    'syntax_operator_fg': '#ffffff',
    'syntax_generic_fg': '#00aaaa',
    'syntax_error_fg': '#aa0000',
    'syntax_comment_fg': '#ffffff',
}

FALLBACK_COLOR_NAMES = {
    'added_fg': 'green',
    'deleted_fg': 'red',
    'hunk_fg': 'cyan',
    'comment_open_fg': 'yellow',
    'comment_superseded_fg': 'cyan',
    'resolved_fg': 'green',
    'dismissed_fg': 'red',
    'visual_fg': 'black',
    'visual_bg': 'cyan',
    'status_fg': 'black',
    'status_bg': 'white',
    'status_active_fg': 'black',
    'status_active_bg': 'yellow',
    'header_fg': 'black',
    'header_bg': 'white',
    'header_active_fg': 'black',
    'header_active_bg': 'yellow',
    'diff_added_bg': 'green',
    'diff_deleted_bg': 'red',
    'comment_open_bg': 'yellow',
    'comment_superseded_bg': 'cyan',
    'current_line_bg': 'black',
    'syntax_keyword_fg': 'cyan',
    'syntax_string_fg': 'yellow',
    'syntax_number_fg': 'magenta',
    'syntax_function_fg': 'blue',
    'syntax_builtin_fg': 'magenta',
    'syntax_namespace_fg': 'cyan',
    'syntax_variable_fg': 'white',
    'syntax_operator_fg': 'white',
    'syntax_generic_fg': 'cyan',
    'syntax_error_fg': 'red',
    'syntax_comment_fg': 'white',
}

CUSTOM_COLOR_IDS = {
    'diff_added_bg': 100,
    'diff_deleted_bg': 101,
    'comment_open_bg': 102,
    'comment_superseded_bg': 103,
    'current_line_bg': 104,
    'added_fg': 105,
    'deleted_fg': 106,
    'hunk_fg': 107,
    'comment_open_fg': 108,
    'comment_superseded_fg': 109,
    'resolved_fg': 110,
    'dismissed_fg': 111,
    'visual_fg': 112,
    'visual_bg': 113,
    'status_fg': 114,
    'status_bg': 115,
    'status_active_fg': 116,
    'status_active_bg': 117,
    'header_fg': 118,
    'header_bg': 119,
    'header_active_fg': 120,
    'header_active_bg': 121,
    'syntax_keyword_fg': 122,
    'syntax_string_fg': 123,
    'syntax_number_fg': 124,
    'syntax_function_fg': 125,
    'syntax_builtin_fg': 126,
    'syntax_namespace_fg': 127,
    'syntax_variable_fg': 128,
    'syntax_operator_fg': 129,
    'syntax_generic_fg': 130,
    'syntax_error_fg': 131,
    'syntax_comment_fg': 132,
}


def default_theme():
    return dict(DEFAULT_THEME)


def theme_config_path(environ=None):
    if environ is None:
        environ = os.environ
    config_home = environ.get('XDG_CONFIG_HOME')
    if config_home:
        return Path(config_home) / 'lrv' / 'theme.yaml'
    home = environ.get('HOME')
    if home:
        return Path(home) / '.config' / 'lrv' / 'theme.yaml'
    return Path.home() / '.config' / 'lrv' / 'theme.yaml'


def load_theme(environ=None):
    theme = default_theme()
    if yaml is None:
        return theme
    path = theme_config_path(environ)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError, UnicodeError):
        return theme
    if not isinstance(data, dict):
        return theme
    for key, value in data.items():
        if key in theme and isinstance(value, str) and is_hex_color(value):
            theme[key] = normalize_hex(value)
    return theme


def is_hex_color(value):
    if len(value) != 7 or not value.startswith('#'):
        return False
    return all(character in '0123456789abcdefABCDEF' for character in value[1:])


def normalize_hex(value):
    return value.lower()


def hex_to_curses_rgb(value):
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    return scale_channel(red), scale_channel(green), scale_channel(blue)


def scale_channel(value):
    return round(value * 1000 / 255)


def hex_to_xterm_256(value, preserve_hue=False):
    red = int(value[1:3], 16)
    green = int(value[3:5], 16)
    blue = int(value[5:7], 16)
    best_color = 0
    best_distance = None
    for color, rgb in xterm_256_palette():
        if preserve_hue and color >= 232 and color <= 255 and color_saturation((red, green, blue)) > 16:
            continue
        distance = color_distance((red, green, blue), rgb)
        if best_distance is None or distance < best_distance:
            best_color = color
            best_distance = distance
    return best_color


def color_distance(left, right):
    return sum((left[index] - right[index]) ** 2 for index in range(3))


def color_saturation(rgb):
    return max(rgb) - min(rgb)


def xterm_256_palette():
    system_colors = (
        (0, (0, 0, 0)),
        (1, (128, 0, 0)),
        (2, (0, 128, 0)),
        (3, (128, 128, 0)),
        (4, (0, 0, 128)),
        (5, (128, 0, 128)),
        (6, (0, 128, 128)),
        (7, (192, 192, 192)),
        (8, (128, 128, 128)),
        (9, (255, 0, 0)),
        (10, (0, 255, 0)),
        (11, (255, 255, 0)),
        (12, (0, 0, 255)),
        (13, (255, 0, 255)),
        (14, (0, 255, 255)),
        (15, (255, 255, 255)),
    )
    for color in system_colors:
        yield color

    levels = (0, 95, 135, 175, 215, 255)
    for red_index, red in enumerate(levels):
        for green_index, green in enumerate(levels):
            for blue_index, blue in enumerate(levels):
                color = 16 + 36 * red_index + 6 * green_index + blue_index
                yield color, (red, green, blue)

    for index in range(24):
        level = 8 + index * 10
        yield 232 + index, (level, level, level)

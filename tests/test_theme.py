import tempfile
import unittest
from pathlib import Path

import lrv.theme as theme


class FakeYamlError(Exception):
    pass


class FakeYaml:
    YAMLError = FakeYamlError

    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error

    def safe_load(self, value):
        if self.error is not None:
            raise self.error
        return self.data


class ThemeTest(unittest.TestCase):
    def setUp(self):
        self.original_yaml = theme.yaml

    def tearDown(self):
        theme.yaml = self.original_yaml

    def test_theme_config_path_uses_xdg_config_home(self):
        path = theme.theme_config_path({'XDG_CONFIG_HOME': '/tmp/config', 'HOME': '/tmp/home'})

        self.assertEqual(path, Path('/tmp/config/lrv/theme.yaml'))

    def test_theme_config_path_falls_back_to_home_config(self):
        path = theme.theme_config_path({'HOME': '/tmp/home'})

        self.assertEqual(path, Path('/tmp/home/.config/lrv/theme.yaml'))

    def test_load_theme_merges_valid_hex_values(self):
        theme.yaml = FakeYaml({
            'added_fg': '#ABCDEF',
            'deleted_fg': 'red',
            'unknown': '#000000',
            'current_line_bg': '#101112',
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lrv' / 'theme.yaml'
            path.parent.mkdir()
            path.write_text('unused\n')

            loaded = theme.load_theme({'XDG_CONFIG_HOME': directory})

        self.assertEqual(loaded['added_fg'], '#abcdef')
        self.assertEqual(loaded['current_line_bg'], '#101112')
        self.assertEqual(loaded['deleted_fg'], theme.DEFAULT_THEME['deleted_fg'])
        self.assertNotIn('unknown', loaded)

    def test_load_theme_falls_back_on_invalid_yaml(self):
        theme.yaml = FakeYaml(error=FakeYamlError())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lrv' / 'theme.yaml'
            path.parent.mkdir()
            path.write_text('unused\n')

            loaded = theme.load_theme({'XDG_CONFIG_HOME': directory})

        self.assertEqual(loaded, theme.DEFAULT_THEME)

    def test_hex_to_curses_rgb_scales_html_channels(self):
        self.assertEqual(theme.hex_to_curses_rgb('#ffffff'), (1000, 1000, 1000))
        self.assertEqual(theme.hex_to_curses_rgb('#000000'), (0, 0, 0))

    def test_hex_to_xterm_256_uses_nearest_palette_color(self):
        self.assertEqual(theme.hex_to_xterm_256('#ff9d4d'), 215)
        self.assertEqual(theme.hex_to_xterm_256('#123f2a'), 235)

    def test_hex_to_xterm_256_can_preserve_hue_for_dark_backgrounds(self):
        self.assertEqual(theme.hex_to_xterm_256('#123f2a', preserve_hue=True), 22)
        self.assertEqual(theme.hex_to_xterm_256('#3f232b', preserve_hue=True), 52)


if __name__ == '__main__':
    unittest.main()

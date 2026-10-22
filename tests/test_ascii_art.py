import os
import subprocess
import sys
import unittest
from unittest import mock

from fluxrecord import ascii_art


class AsciiArtTest(unittest.TestCase):
    def test_every_style_renders(self):
        for style in ascii_art.STYLES:
            art = ascii_art.get_art(style)
            self.assertTrue(art.strip(), style)
            self.assertNotIn("\t", art, style)

    def test_unknown_style_falls_back_to_the_hero(self):
        self.assertEqual(ascii_art.get_art("does-not-exist"), ascii_art.ASCII_ART)

    def test_lines_carry_no_trailing_whitespace(self):
        for style in ascii_art.STYLES:
            for line in ascii_art.get_art(style).splitlines():
                self.assertEqual(line, line.rstrip(), style)

    def test_hero_lockup_is_wide_and_aligned(self):
        lines = ascii_art.ASCII_ART.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(len(lines[0]), len(lines[1]))
        self.assertEqual(len(lines[1]), len(lines[4]))
        self.assertIn(ascii_art.TAGLINE, lines[5])

    def test_ascii_fallback_uses_printable_ascii_only(self):
        for line in ascii_art.ASCII_ASCII.splitlines():
            self.assertTrue(line.isascii(), line)

    def test_minimal_style_is_the_bare_name(self):
        self.assertEqual(ascii_art.get_art("minimal"), "fluxrecord")

    def test_color_wraps_the_mark_in_ansi(self):
        colored = ascii_art.get_art("large", color=True)
        self.assertIn("\x1b[", colored)
        self.assertIn("\x1b[0m", colored)

    def test_color_is_skipped_when_no_color_is_set(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertEqual(ascii_art.get_art("large", color=True), ascii_art.ASCII_ART)

    def test_color_is_skipped_for_styles_without_a_color_form(self):
        self.assertEqual(ascii_art.get_art("ascii", color=True), ascii_art.ASCII_ASCII)

    def test_cli_prints_the_requested_style(self):
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", "art", "--style", "mini"],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("fluxrecord", result.stdout.decode())
        self.assertNotIn("\x1b[", result.stdout.decode())

    def test_cli_rejects_unknown_style(self):
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", "art", "--style", "nope"],
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()

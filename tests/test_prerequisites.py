"""tools/prerequisites.py's lookups, and the config checks for its tool entries.

Run: python3 -m unittest discover tests
"""

import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import prerequisites as pre  # noqa: E402
import repo_kit_config as kit  # noqa: E402

POSIX = os.name == "posix"


def fake_tool(folder: Path, name: str, output: str, code: int = 0) -> Path:
    """A program that prints a version line, as a shell script (POSIX hosts only)."""
    path = folder / name
    path.write_text(f"#!/bin/sh\necho '{output}'\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Platform(unittest.TestCase):
    def test_platform_names(self):
        self.assertEqual(pre.platform_of("darwin"), "mac")
        for name in ("win32", "cygwin", "msys"):
            self.assertEqual(pre.platform_of(name), "windows")
        self.assertEqual(pre.platform_of("linux"), "linux")
        self.assertEqual(pre.platform_of("freebsd14"), "linux")

    def test_every_core_tool_has_an_install_command_for_every_platform(self):
        for tool in pre.KIT_CORE:
            for platform in pre.PLATFORMS:
                self.assertTrue(tool.get(platform), f"{tool['name']} on {platform}")
        python = next(t for t in pre.KIT_CORE if t["name"] == "python3")
        self.assertEqual(python["aliases"], ["python", "py"])
        kit.prerequisites({"prerequisites": {"groups": {"core": {"tools": pre.KIT_CORE}}}})

    def test_version_of_a_godot_build(self):
        self.assertEqual(pre.VERSION.search("4.7.2.stable.official.abc123").group(1), "4.7.2")
        self.assertEqual(pre.VERSION.search("Godot Engine v4.7.2.stable.mono").group(1), "4.7.2")


class Expand(unittest.TestCase):
    def test_home_and_variables(self):
        with mock.patch.dict(os.environ, {"KIT_TEST_DIR": "/opt/kit", "HOME": "/home/me"}):
            self.assertEqual(pre.expand("$KIT_TEST_DIR/bin"), "/opt/kit/bin")
            self.assertEqual(pre.expand("${KIT_TEST_DIR}/bin"), "/opt/kit/bin")
            if POSIX:
                self.assertEqual(pre.expand("~/bin"), "/home/me/bin")

    @unittest.skipUnless(os.name == "nt", "%VAR% is expanded by Windows' os.path.expandvars only")
    def test_percent_variables_on_windows(self):
        with mock.patch.dict(os.environ, {"KIT_TEST_DIR": "C:\\kit"}):
            self.assertEqual(pre.expand("%KIT_TEST_DIR%\\godot.exe"), "C:\\kit\\godot.exe")


@unittest.skipUnless(POSIX, "the fake tools are shell scripts")
class Lookup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(os.path.realpath(self.tmp.name))
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        # Only the fake bin folder on PATH, so the host's own tools never answer.
        self.env = mock.patch.dict(os.environ, {"PATH": str(self.bin)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_found_on_path_reports_just_the_version(self):
        fake_tool(self.bin, "godot", "4.7.2.stable.official.abc123")
        self.assertEqual(pre.check({"name": "godot", "min": "4.7"}), (True, "4.7.2"))

    def test_missing(self):
        self.assertEqual(pre.check({"name": "godot"}), (False, "missing"))

    def test_too_old(self):
        fake_tool(self.bin, "godot", "4.6.1.stable")
        self.assertEqual(pre.check({"name": "godot", "min": "4.7"}), (False, "4.6.1, need 4.7 or newer"))

    def test_env_wins_and_is_named(self):
        fake_tool(self.bin, "godot", "4.6.0")
        own = fake_tool(self.dir, "Godot", "4.7.2.stable")
        with mock.patch.dict(os.environ, {"GODOT": str(own)}):
            self.assertEqual(pre.check({"name": "godot", "env": "GODOT", "min": "4.7"}),
                             (True, f"4.7.2 ({own})"))

    def test_env_naming_a_program_on_path(self):
        path = fake_tool(self.bin, "godot-beta", "4.8.0")
        with mock.patch.dict(os.environ, {"GODOT": "godot-beta"}):
            self.assertEqual(pre.check({"name": "godot", "env": "GODOT"}), (True, f"4.8.0 ({path})"))

    def test_env_set_to_nothing_that_runs_fails(self):
        fake_tool(self.bin, "godot", "4.7.2")
        with mock.patch.dict(os.environ, {"GODOT": str(self.dir / "nope")}):
            ok, report = pre.check({"name": "godot", "env": "GODOT"})
        self.assertFalse(ok)
        self.assertIn("GODOT=", report)

    def test_unset_env_falls_through_to_path(self):
        fake_tool(self.bin, "godot", "4.7.2")
        with mock.patch.dict(os.environ, {"GODOT": ""}):
            self.assertEqual(pre.check({"name": "godot", "env": "GODOT"}), (True, "4.7.2"))

    def test_alias(self):
        path = fake_tool(self.bin, "python", "Python 3.12.1")
        self.assertEqual(pre.check({"name": "python3", "min": "3.9", "aliases": ["python", "py"]}),
                         (True, f"3.12.1 ({path})"))

    def test_a_stand_in_that_does_not_run_gives_way_to_an_alias(self):
        # Windows' python3 can be the Store's stand-in: it is on PATH but prints no version.
        fake_tool(self.bin, "python3", "Python was not found; run without arguments to install", 9009)
        path = fake_tool(self.bin, "py", "Python 3.12.1")
        self.assertEqual(pre.check({"name": "python3", "min": "3.9", "aliases": ["python", "py"]}),
                         (True, f"3.12.1 ({path})"))

    def test_too_old_first_then_a_newer_alias(self):
        fake_tool(self.bin, "python3", "Python 3.8.10")
        path = fake_tool(self.bin, "python", "Python 3.11.0")
        self.assertEqual(pre.check({"name": "python3", "min": "3.9", "aliases": ["python"]}),
                         (True, f"3.11.0 ({path})"))

    def test_all_too_old_reports_the_first(self):
        fake_tool(self.bin, "python3", "Python 3.8.10")
        self.assertEqual(pre.check({"name": "python3", "min": "3.9", "aliases": ["python"]}),
                         (False, "3.8.10, need 3.9 or newer"))

    def test_paths_for_this_platform_with_a_glob_take_the_last_match(self):
        apps = self.dir / "Applications"
        for version in ("4.6.1", "4.7.2"):
            folder = apps / f"Godot_v{version}.app/Contents/MacOS"
            folder.mkdir(parents=True)
            fake_tool(folder, "Godot", f"{version}.stable.official")
        tool = {"name": "godot", "paths": {pre.PLATFORM: ["$KIT_APPS/Godot_v*.app/Contents/MacOS/Godot"]}}
        with mock.patch.dict(os.environ, {"KIT_APPS": str(apps)}):
            ok, report = pre.check(tool)
        self.assertTrue(ok)
        self.assertEqual(report, f"4.7.2 ({apps}/Godot_v4.7.2.app/Contents/MacOS/Godot)")

    def test_paths_of_another_platform_are_ignored(self):
        fake_tool(self.dir, "Godot", "4.7.2")
        other = next(p for p in pre.PLATFORMS if p != pre.PLATFORM)
        self.assertEqual(pre.check({"name": "godot", "paths": {other: [str(self.dir / "Godot")]}}),
                         (False, "missing"))

    def test_a_file_that_is_not_executable_is_not_a_candidate(self):
        (self.dir / "Godot").write_text("not a program")
        self.assertEqual(pre.from_paths([str(self.dir / "Godot")]), [])

    def test_path_beats_paths(self):
        fake_tool(self.bin, "godot", "4.7.2")
        other = fake_tool(self.dir, "Godot", "4.8.0")
        self.assertEqual(pre.check({"name": "godot", "paths": {pre.PLATFORM: [str(other)]}}),
                         (True, "4.7.2"))

    def test_on_windows_the_windows_entries_are_used(self):
        fake_tool(self.dir, "godot.exe", "4.7.2")
        tool = {"name": "godot", "paths": {"windows": [str(self.dir / "godot.exe")], "mac": []}}
        with mock.patch.object(pre, "PLATFORM", "windows"):
            self.assertEqual(pre.check(tool), (True, f"4.7.2 ({self.dir / 'godot.exe'})"))


class Schema(unittest.TestCase):
    def config(self, **tool):
        return {"prerequisites": {"groups": {"native": {"tools": [{"name": "godot", **tool}]}}}}

    def assertRefused(self, **tool):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            kit.prerequisites(self.config(**tool))

    def test_full_entry_is_accepted(self):
        kit.prerequisites(self.config(
            min="4.7", args=["--version", "--headless"], env="GODOT", aliases=["godot4"],
            mac="brew install --cask godot", linux="flatpak install godot",
            windows="winget install GodotEngine.GodotEngine", then="godot --editor",
            paths={"mac": ["/Applications/Godot.app/Contents/MacOS/Godot"],
                   "windows": ["%LOCALAPPDATA%/Godot/Godot_v*_win64.exe"], "linux": ["~/bin/godot"]}))

    def test_bad_types_are_refused(self):
        self.assertRefused(windows=["winget"])
        self.assertRefused(windows="")
        self.assertRefused(env="NOT A NAME")
        self.assertRefused(env=["GODOT"])
        self.assertRefused(aliases="godot4")
        self.assertRefused(aliases=["godot4", ""])
        self.assertRefused(paths=["/Applications/Godot.app"])
        self.assertRefused(paths={"win": ["C:/godot.exe"]})
        self.assertRefused(paths={"windows": "C:/godot.exe"})
        self.assertRefused(args=["--version", 1])
        self.assertRefused(min="4.x")


if __name__ == "__main__":
    unittest.main()

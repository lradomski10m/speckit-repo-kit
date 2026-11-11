"""What keeps a repository working when it's also checked out on Windows.

Run: python3 -m unittest discover tests. These run on any host: Windows' behaviour (no symbolic
links, CRLF checkouts) is simulated.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import repo_kit_config as kit  # noqa: E402


def no_symlinks(*args, **kwargs):
    raise OSError(1314, "A required privilege is not held by the client")


class Skills(unittest.TestCase):
    """Skills copied from a local folder, linked (or copied) for Claude Code."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "repo"
        self.repo.mkdir()
        self.source = root / "skills/demo"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: demo\n---\nOne.\n")
        self.config = {"skills": [{"name": "demo", "source": "../skills/demo"}]}
        # Only this skill: the kit's own would need the network.
        patcher = mock.patch.object(kit, "KIT_SKILLS", root / "none.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self):
        return kit.apply_skills(self.config, self.repo, self.repo, True)

    @property
    def claude(self) -> Path:
        return self.repo / ".claude/skills/demo"

    def test_a_link_where_links_can_be_made(self):
        self.assertIn("linked .claude/skills/demo", self.apply())
        self.assertTrue(self.claude.is_symlink())
        self.assertEqual(self.apply(), [])

    def test_a_copy_where_they_cannot(self):
        with mock.patch.object(Path, "symlink_to", no_symlinks):
            changed = self.apply()
            self.assertTrue(any(c.startswith("copied .claude/skills/demo") for c in changed), changed)
            self.assertTrue(self.claude.is_dir() and not self.claude.is_symlink())
            self.assertEqual((self.claude / "SKILL.md").read_text(), "---\nname: demo\n---\nOne.\n")
            self.assertEqual(self.apply(), [], "a current copy is quietly kept")
            (self.source / "SKILL.md").write_text("---\nname: demo\n---\nTwo.\n")
            self.apply()
            self.assertIn("Two.", (self.claude / "SKILL.md").read_text(), "the copy follows the skill")
        self.assertIn("linked .claude/skills/demo", self.apply(), "a link replaces it once possible")
        self.assertTrue(self.claude.is_symlink())

    def test_gits_placeholder_for_a_link_is_replaced(self):
        # A checkout without symbolic links writes the link as a file holding its target.
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text("../../.agents/skills/demo")
        self.assertIn("linked .claude/skills/demo", self.apply())
        self.assertTrue(self.claude.is_symlink())

    def test_a_folder_the_kit_did_not_make_is_refused(self):
        self.claude.mkdir(parents=True)
        (self.claude / "SKILL.md").write_text("mine")
        with self.assertRaises(SystemExit):
            self.apply()

    def test_a_removed_skill_takes_its_copy_away(self):
        with mock.patch.object(Path, "symlink_to", no_symlinks):
            self.apply()
        self.config = {"skills": []}
        self.assertIn("removed skill demo", self.apply())
        self.assertFalse(self.claude.exists())

    def test_a_crlf_checkout_is_not_an_edit(self):
        self.apply()
        skill = self.repo / ".agents/skills/demo/SKILL.md"
        skill.write_bytes(skill.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.apply(), [])

    def test_a_real_edit_still_is(self):
        self.apply()
        (self.repo / ".agents/skills/demo/SKILL.md").write_text("edited\n")
        with self.assertRaises(SystemExit):
            self.apply()


class LineEnds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_bytes_reads_crlf_as_lf_but_leaves_binary(self):
        text, binary = self.repo / "a.txt", self.repo / "b.bin"
        text.write_bytes(b"a\r\nb\r\n")
        binary.write_bytes(b"\0\r\n")
        self.assertEqual(kit.text_bytes(text), b"a\nb\n")
        self.assertEqual(kit.text_bytes(binary), b"\0\r\n")

    def test_written_files_have_lf(self):
        kit.write_json(self.repo / "x.json", {"a": 1})
        self.assertNotIn(b"\r", (self.repo / "x.json").read_bytes())

    def test_gitattributes_keep_shell_scripts_lf(self):
        (self.repo / ".gitattributes").write_text("*.png binary")
        self.assertEqual(kit.apply_gitattributes(self.repo), [".gitattributes: *.sh text eol=lf"])
        self.assertEqual(kit.apply_gitattributes(self.repo), [])
        self.assertEqual((self.repo / ".gitattributes").read_text(), "*.png binary\n*.sh text eol=lf\n")

    def test_a_crlf_finalize_config_is_not_an_edit(self):
        kit_file = self.repo / "kit.yml"
        kit_file.write_text("steps:\n  - run: $ make test\n")
        kit.sync_file(kit_file, {}, self.repo, "out.yml")
        out = self.repo / "out.yml"
        out.write_bytes(out.read_bytes().replace(b"\n", b"\r\n"))
        kit_file.write_text("steps:\n  - run: $ make test\n  - run: $ make verify\n")
        self.assertEqual(kit.sync_file(kit_file, {}, self.repo, "out.yml"),
                         ["out.yml: updated to the kit's version"])

    def test_a_crlf_hook_launcher_is_rewritten(self):
        config = {"agents": ["agy"], "permissions": {"network": "off"}}
        kit.apply_agy(config, self.repo, "off")
        launcher = self.repo / kit.AGY_LAUNCHER
        launcher.write_bytes(launcher.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(kit.apply_agy(config, self.repo, "off"),
                         [f"{kit.AGY_LAUNCHER}: the kit's approval hook"])
        self.assertNotIn(b"\r", launcher.read_bytes())


class HookCommand(unittest.TestCase):
    """The committed hook line, run the way Antigravity's CLI runs it on macOS: `sh -c` in .agents/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self.tmp.name)) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        kit.apply_agy({"agents": ["agy"], "permissions": {"network": "full", "commands": "all"}},
                      self.repo, "full")
        hooks = json.loads((self.repo / kit.AGY_HOOKS).read_text())[kit.AGY_HOOK]["PreToolUse"]
        self.commands = {h["hooks"][0]["command"].rsplit(" ", 1)[1]: h["hooks"][0]["command"] for h in hooks}

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, group, call, cwd=None):
        out = subprocess.run(["sh", "-c", self.commands[group]], input=json.dumps({"toolCall": call}),
                             capture_output=True, text=True, check=True, cwd=cwd or self.repo / ".agents")
        return json.loads(out.stdout)["decision"]

    def test_the_line_has_nothing_only_one_shell_reads(self):
        for command in self.commands.values():
            for token in ("$", "`", "&&", "||", ";", "'", "%"):
                self.assertNotIn(token, command)

    @unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
    def test_runs_from_dot_agents_and_from_anywhere_in_the_checkout(self):
        inside = {"name": "write_file", "args": {"Path": str(self.repo / "a.txt")}}
        self.assertEqual(self.run_hook("files", inside), "auto_approve")
        deep = self.repo / "src/deep"
        deep.mkdir(parents=True)
        self.assertEqual(self.run_hook("files", inside, cwd=deep), "auto_approve")
        self.assertEqual(self.run_hook("files", {"name": "write_file", "args": {"Path": "/etc/x"}}), "allow")
        self.assertEqual(self.run_hook("commands", {"name": "run_command", "args": {}}), "auto_approve")
        self.assertEqual(self.run_hook("network", {"name": "search_web", "args": {}}), "auto_approve")

    @unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
    def test_without_a_python_the_call_is_left_to_antigravity(self):
        # A PATH with git and sh but no Python at all.
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        for tool in ("git", "sh", "dirname"):
            (bin_dir / tool).symlink_to(shutil.which(tool))
        env = {**os.environ, "PATH": str(bin_dir)}
        out = subprocess.run(["sh", "-c", self.commands["commands"]], input="{}", capture_output=True,
                             text=True, check=True, cwd=self.repo / ".agents", env=env)
        self.assertEqual(json.loads(out.stdout)["decision"], "allow")


if __name__ == "__main__":
    unittest.main()

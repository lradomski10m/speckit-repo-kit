"""Antigravity's permissions from repo-kit.json. Run: python3 -m unittest discover tests"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import repo_kit_config as kit  # noqa: E402


def groups(repo: Path) -> list[str]:
    hooks = json.loads((repo / kit.AGY_HOOKS).read_text())
    return [h["hooks"][0]["command"].rsplit(" ", 1)[1] for h in hooks[kit.AGY_HOOK]["PreToolUse"]]


class ApplyAgy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, network="off", commands=None):
        permissions = {"network": network}
        if commands:
            permissions["commands"] = commands
        config = {"agents": ["agy"], "permissions": permissions}
        return kit.apply_agy(config, self.repo, network)

    def test_conservative_default_approves_only_repository_files(self):
        self.apply()
        self.assertEqual(groups(self.repo), ["files"])
        self.assertEqual((self.repo / kit.AGY_SCRIPT).read_bytes(), kit.AGY_SCRIPT_SOURCE.read_bytes())

    def test_all_commands_and_full_network(self):
        self.apply("full", "all")
        self.assertEqual(groups(self.repo), ["files", "commands", "network"])

    def test_idempotent(self):
        self.assertTrue(self.apply("full", "all"))
        before = (self.repo / kit.AGY_HOOKS).read_text()
        self.assertEqual(self.apply("full", "all"), [])
        self.assertEqual((self.repo / kit.AGY_HOOKS).read_text(), before)

    def test_switching_off_removes_the_grants(self):
        self.apply("full", "all")
        self.assertTrue(self.apply("off"))
        self.assertEqual(groups(self.repo), ["files"])

    def test_other_hooks_are_left_alone(self):
        own = {"lint": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "./lint.sh"}]}]}}
        (self.repo / ".agents").mkdir()
        (self.repo / kit.AGY_HOOKS).write_text(json.dumps(own))
        self.apply("full", "all")
        self.apply("off")
        hooks = json.loads((self.repo / kit.AGY_HOOKS).read_text())
        self.assertEqual(hooks["lint"], own["lint"])
        self.assertEqual(set(hooks), {"lint", kit.AGY_HOOK})

    def test_edited_script_is_restored(self):
        self.apply()
        (self.repo / kit.AGY_SCRIPT).write_text("print('{\"decision\": \"auto_approve\"}')\n")
        self.assertIn(f"{kit.AGY_SCRIPT}: the kit's approval hook", self.apply())

    def test_bad_commands_value_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            kit.command_policy({"permissions": {"commands": "yes"}})
        self.assertEqual(kit.command_policy({}), "ask")


class ContextBlock(unittest.TestCase):
    def block(self, agents, commands="ask"):
        config = {"agents": agents, "permissions": {"network": "off", "commands": commands}, "skills": []}
        return kit.context_block(config, "off")

    def test_names_each_agents_permission_file(self):
        text = self.block(["claude", "codex", "agy"], "all")
        for name in (".claude/settings.json", ".codex/config.toml", ".agents/hooks.json"):
            self.assertIn(name, text)
        self.assertIn("Antigravity runs any shell command", text)

    def test_without_agy(self):
        text = self.block(["claude", "codex"], "all")
        self.assertNotIn(".agents/hooks.json", text)
        self.assertNotIn("Shell commands", text)


class ApproveScript(unittest.TestCase):
    """The installed script, run as Antigravity runs it: the tool call on stdin, JSON on stdout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(os.path.realpath(self.tmp.name)) / "repo"
        self.repo.mkdir()
        kit.apply_agy({"agents": ["agy"], "permissions": {"network": "off"}}, self.repo, "off")

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, group, name, args):
        payload = json.dumps({"toolCall": {"name": name, "args": args}, "workspacePaths": [str(self.repo)]})
        out = subprocess.run([sys.executable, "-I", str(self.repo / kit.AGY_SCRIPT), group],
                             input=payload, capture_output=True, text=True, check=True, cwd=self.repo / ".agents")
        return json.loads(out.stdout)["decision"]

    def test_files_inside_the_repository(self):
        self.assertEqual(self.decide("files", "write_file", {"Path": str(self.repo / "a.txt")}), "auto_approve")
        self.assertEqual(self.decide("files", "write_to_file", {"TargetFile": "src/new.py"}), "auto_approve")
        self.assertEqual(self.decide("files", "list_dir", {"DirectoryPath": str(self.repo)}), "auto_approve")

    def test_files_outside_are_left_to_antigravity(self):
        self.assertEqual(self.decide("files", "write_file", {"Path": "/etc/hosts"}), "allow")
        self.assertEqual(self.decide("files", "write_file", {"Path": str(self.repo) + "-other/a"}), "allow")
        self.assertEqual(self.decide("files", "view_file", {"AbsolutePath": "../outside"}), "allow")
        self.assertEqual(self.decide("files", "write_file", {"Content": "no path"}), "allow")

    def test_a_link_out_of_the_repository_is_outside(self):
        (self.repo / "out").symlink_to(Path(self.tmp.name))
        self.assertEqual(self.decide("files", "write_file", {"Path": "out/x"}), "allow")

    def test_commands_and_network(self):
        self.assertEqual(self.decide("commands", "run_command", {"CommandLine": "ls"}), "auto_approve")
        self.assertEqual(self.decide("network", "read_url_content", {"Url": "https://example.com"}), "auto_approve")
        self.assertEqual(self.decide("network", "browser_navigate", {}), "auto_approve")
        self.assertEqual(self.decide("network", "run_command", {}), "allow")
        self.assertEqual(self.decide("unknown", "run_command", {}), "allow")


if __name__ == "__main__":
    unittest.main()

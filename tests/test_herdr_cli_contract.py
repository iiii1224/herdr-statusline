"""What the hsl classifier assumes about herdr's CLI, and how far it is checked.

Layer 2 (this file, always run) checks the committed 0.8.0 snapshot. It cannot
detect that the snapshot itself has gone stale, and it cannot prove which
subcommands carry `attach`: `herdr --help` does not list `plugin` at all, so a
set derived from help output is not exhaustive. Layer 3 compares the snapshot
against a real binary and is deselected by default.
"""

import os
import pathlib
import re
import shutil
import subprocess
import unittest

import pytest

from tests.helpers import ROOT

FIXTURES = ROOT / "tests/fixtures/herdr-0.8.0"
ATTACH_SUBCOMMANDS = {"session", "agent", "terminal"}
# Every top-level subcommand discoverable from the root help, plus `plugin`,
# which the root help does not list at all even though hsl depends on it.
# This set is what the fixture must contain -- it is NOT a claim that herdr
# has no other subcommands.
EXPECTED_FIXTURES = {
    "update", "server", "status", "completion", "api", "channel", "config",
    "workspace", "worktree", "tab", "notification", "agent", "pane",
    "session", "integration", "terminal", "plugin",
}
GLOBAL_OPTIONS = {
    "--no-session", "--session", "--remote", "--remote-keybindings",
    "--handoff", "--default-config", "--skill", "--version", "-V",
    "--help", "-h",
}


def normalize(text):
    home = os.environ.get("HOME", "")
    if home:
        text = text.replace(home, "$HOME")
    return "\n".join(line.rstrip() for line in text.splitlines())


def global_options(root_help):
    """Options from the `Options:` block of the root help."""
    block = root_help.split("Options:", 1)
    if len(block) < 2:
        return set()
    return set(re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", block[1]))


def has_attach(subcommand_help):
    return bool(re.search(r"^\s+attach\s", subcommand_help, re.M))


class SnapshotTests(unittest.TestCase):
    """Layer 2. Runs everywhere; needs no herdr binary."""

    def setUp(self):
        self.root = (FIXTURES / "root.txt").read_text()

    def test_the_snapshot_carries_no_absolute_paths(self):
        for path in FIXTURES.glob("*.txt"):
            with self.subTest(fixture=path.name):
                self.assertNotIn(os.environ.get("HOME", "\0"), path.read_text())

    def test_global_options_match_exactly(self):
        # The root help's Options: block is complete, so this one can be exact.
        self.assertEqual(global_options(self.root), GLOBAL_OPTIONS)

    def test_the_three_known_attach_forms_are_present(self):
        for name in sorted(ATTACH_SUBCOMMANDS):
            with self.subTest(subcommand=name):
                self.assertTrue(has_attach((FIXTURES / f"{name}.txt").read_text()))

    def test_no_other_captured_subcommand_offers_attach(self):
        # Best effort, NOT exhaustive: herdr --help does not list `plugin`, so
        # a set built from help output cannot prove which commands exist.
        captured = {p.stem for p in FIXTURES.glob("*.txt")} - {"root"}
        for name in sorted(captured - ATTACH_SUBCOMMANDS):
            with self.subTest(subcommand=name):
                self.assertFalse(has_attach((FIXTURES / f"{name}.txt").read_text()))

    def test_the_fixture_holds_exactly_the_expected_subcommands(self):
        # Without an exact set the negative test above passes vacuously on a
        # fixture that captured only the three attach commands. This is an
        # exact match on "root-discoverable plus the stated known exception",
        # NOT a claim about the exhaustive set of herdr subcommands.
        captured = {p.stem for p in FIXTURES.glob("*.txt")} - {"root"}
        self.assertEqual(captured, EXPECTED_FIXTURES)


@pytest.mark.live_herdr
class LiveComparisonTests(unittest.TestCase):
    """Layer 3. Deselected by default; select with `-m live_herdr`.

    Fails rather than skips when herdr is absent: this test is only ever run
    because someone asked for it, and answering "skipped" to that request is
    the silent-green failure the suite forbids.
    """

    def setUp(self):
        self.herdr = shutil.which("herdr")
        self.assertIsNotNone(
            self.herdr,
            "herdr is not installed; this test was explicitly selected so it "
            "fails rather than skipping",
        )

    def capture(self, *args):
        return normalize(
            subprocess.run(
                [self.herdr, *args], text=True, capture_output=True
            ).stdout
        )

    def test_root_help_matches_the_snapshot(self):
        self.assertEqual(
            self.capture("--help"), normalize((FIXTURES / "root.txt").read_text())
        )

    def test_each_captured_subcommand_matches_the_snapshot(self):
        for path in sorted(FIXTURES.glob("*.txt")):
            if path.stem == "root":
                continue
            with self.subTest(subcommand=path.stem):
                self.assertEqual(
                    self.capture(path.stem, "--help"),
                    normalize(path.read_text()),
                )

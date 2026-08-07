import os
import pathlib
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, MANAGED_MARKER, base_env, make_executable

BUILD = ROOT / "scripts/build.sh"


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.home = self.base / "home"
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir(parents=True)
        self.cargo_log = self.base / "cargo.log"
        make_executable(
            self.fakebin / "cargo",
            '#!/bin/sh\nprintf \'%s\\n\' "$*" > "$HSL_TEST_CARGO_LOG"\nexit 0\n',
        )
        self.bindir = self.home / ".local/bin"
        self.env = base_env(self.home, self.fakebin)
        self.env.update(
            {
                "HSL_TEST_CARGO_BIN": str(self.fakebin / "cargo"),
                "HSL_TEST_BIN_DIR": str(self.bindir),
                "HSL_TEST_CARGO_LOG": str(self.cargo_log),
                "HSL_TEST_SKIP_BINARY_CHECK": "1",
            }
        )

    def run_build(self):
        return subprocess.run(
            ["sh", str(BUILD)], cwd=ROOT, env=self.env, text=True, capture_output=True
        )

    def test_manifest_declares_the_standard_linux_build(self):
        text = (ROOT / "herdr-plugin.toml").read_text()
        self.assertIn('id = "herdr-statusline"', text)
        self.assertIn('min_herdr_version = "0.7.5"', text)
        self.assertIn('platforms = ["linux"]', text)
        self.assertIn('command = ["sh", "scripts/build.sh"]', text)

    def test_runs_a_locked_release_build_and_writes_a_managed_launcher(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cargo_log.read_text().strip(), "build --release --locked")
        launcher = self.bindir / "hsl"
        self.assertTrue(launcher.is_file())
        text = launcher.read_text()
        self.assertIn(MANAGED_MARKER, text)
        self.assertIn(str(ROOT.resolve()), text)
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_refuses_an_unmanaged_file_and_explains_the_fix(self):
        self.bindir.mkdir(parents=True)
        target = self.bindir / "hsl"
        target.write_text("unmanaged\n")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), "unmanaged\n")
        self.assertIn("remove or rename", result.stderr)

    def test_refuses_a_symlink(self):
        self.bindir.mkdir(parents=True)
        target = self.bindir / "hsl"
        target.symlink_to(self.base / "elsewhere")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(target.is_symlink())

    def test_replaces_only_a_managed_launcher(self):
        self.bindir.mkdir(parents=True)
        launcher = self.bindir / "hsl"
        launcher.write_text(f"#!/bin/sh\n{MANAGED_MARKER}\nold-body\n")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("old-body", launcher.read_text())

    def test_fails_without_cargo(self):
        self.env["HSL_TEST_CARGO_BIN"] = str(self.base / "no-such-cargo")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cargo", result.stderr)
        self.assertFalse((self.bindir / "hsl").exists())

    def test_fails_when_the_customization_skill_is_missing(self):
        import shutil

        staged = self.base / "plugin"
        shutil.copytree(
            ROOT, staged, ignore=shutil.ignore_patterns(".git", "target", "__pycache__")
        )
        skill = staged / "skills/customize-herdr-statusline/SKILL.md"
        skill.unlink()

        result = subprocess.run(
            ["sh", "scripts/build.sh"],
            cwd=staged,
            env=self.env,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skills/customize-herdr-statusline/SKILL.md", result.stderr)
        self.assertFalse((self.bindir / "hsl").exists())

    def test_restores_missing_execute_bits_instead_of_failing(self):
        staged = self.base / "plugin"
        import shutil

        shutil.copytree(
            ROOT, staged, ignore=shutil.ignore_patterns(".git", "target", "__pycache__")
        )
        for name in (
            "bin/hsl-internal",
            "scripts/run-in-tmux",
        ):
            (staged / name).chmod(0o644)
        result = subprocess.run(
            ["sh", "scripts/build.sh"],
            cwd=staged,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("bin/hsl-internal", "scripts/run-in-tmux"):
            self.assertTrue(os.access(staged / name, os.X_OK), name)

    def test_leaves_no_partial_launcher_when_the_build_fails(self):
        make_executable(self.fakebin / "cargo", "#!/bin/sh\nexit 3\n")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.bindir / "hsl").exists())
        if self.bindir.exists():
            self.assertEqual([p.name for p in self.bindir.iterdir()], [])

    def test_readme_documents_only_the_standard_workflow(self):
        text = (ROOT / "README.md").read_text()
        self.assertIn("herdr plugin install iiii1224/herdr-statusline", text)
        self.assertIn("hsl uninstall --purge", text)
        self.assertIn("herdr plugin config-dir herdr-statusline", text)
        self.assertNotIn("./install.sh", text)
        self.assertNotIn(".local/share/herdr-statusline", text)

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, base_env, ensure_helper, make_executable

FAKE_INTERNAL = """#!/bin/sh
python3 - "$@" <<'PY'
import json, os, pathlib, sys
pathlib.Path(os.environ['HSL_TEST_INTERNAL_LOG']).write_text(json.dumps(sys.argv[1:]))
PY
"""

FAKE_HERDR = """#!/bin/sh
python3 - "$@" <<'PY'
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with pathlib.Path(os.environ['HSL_TEST_HERDR_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['plugin', 'list']:
    # Herdr only knows a plugin root once HSL_TEST_REGISTERED_ROOT says so, and
    # it prints the compact JSON that Herdr 0.7.5 really emits.
    root = os.environ.get('HSL_TEST_REGISTERED_ROOT', '')
    plugins = [] if not root else [{
        'plugin_id': 'herdr-statusline', 'plugin_root': root, 'enabled': True,
    }]
    print(json.dumps({'id': 'cli:plugin',
                      'result': {'plugins': plugins, 'type': 'plugin_list'}},
                     separators=(',', ':')))
    raise SystemExit(int(os.environ.get('HSL_TEST_PLUGIN_LIST_EXIT', '0')))
if args == ['plugin', 'config-dir', 'herdr-statusline']:
    print(os.environ['HSL_TEST_CONFIG_DIR'])
    raise SystemExit(0)
if args == ['plugin', 'uninstall', 'herdr-statusline']:
    if os.environ.get('HSL_TEST_UNINSTALL_FAIL') == '1':
        print('plugin uninstall failed', file=sys.stderr)
        raise SystemExit(19)
    shutil.rmtree(os.environ['HSL_TEST_PLUGIN_ROOT'], ignore_errors=True)
    replacement = os.environ.get('HSL_TEST_REPLACE_LAUNCHER')
    if replacement:
        pathlib.Path(replacement).write_text('#!/bin/sh\\n# unmanaged replacement\\n')
    raise SystemExit(0)
raise SystemExit(0)
PY
"""


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_helper()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.home = self.base / "home"
        self.plugin_root = self.base / "plugin"
        shutil.copytree(
            ROOT,
            self.plugin_root,
            ignore=shutil.ignore_patterns(".git", "target", "__pycache__"),
        )
        helper = self.plugin_root / "target/release/hsl-config"
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "target/release/hsl-config", helper)
        helper.chmod(0o755)
        make_executable(self.plugin_root / "bin/hsl-internal", FAKE_INTERNAL)

        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        make_executable(self.fakebin / "cargo", "#!/bin/sh\nexit 0\n")
        make_executable(self.fakebin / "herdr", FAKE_HERDR)
        self.config_dir = self.home / ".config/herdr/plugins/config/herdr-statusline"
        self.env = base_env(self.home, self.fakebin)
        self.env.update(
            {
                "HSL_TEST_CARGO_BIN": str(self.fakebin / "cargo"),
                "HSL_TEST_SKIP_BINARY_CHECK": "1",
                "HSL_HERDR_BIN": str(self.fakebin / "herdr"),
                "HSL_TEST_INTERNAL_LOG": str(self.base / "internal.json"),
                "HSL_TEST_HERDR_LOG": str(self.base / "herdr.jsonl"),
                "HSL_TEST_PLUGIN_ROOT": str(self.plugin_root),
                "HSL_TEST_CONFIG_DIR": str(self.config_dir),
            }
        )
        subprocess.run(
            ["sh", "scripts/build.sh"],
            cwd=self.plugin_root,
            env=self.env,
            text=True,
            check=True,
            capture_output=True,
        )
        self.launcher = self.home / ".local/bin/hsl"

    def run_launcher(self, *args, **extra_env):
        env = self.env.copy()
        env.update(extra_env)
        return subprocess.run(
            [str(self.launcher), *args], env=env, text=True, capture_output=True
        )

    def make_config_dir(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        (self.config_dir / "config.toml").write_text("enabled = true\n")
        (self.config_dir / "herdr-info.sh").write_text("#!/bin/sh\necho mine\n")
        return self.config_dir

    def internal_args(self):
        return json.loads((self.base / "internal.json").read_text())

    def herdr_calls(self):
        path = self.base / "herdr.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_passes_original_arguments_to_the_internal_launcher(self):
        result = self.run_launcher("--session", "name with space")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.internal_args(), ["--session", "name with space"])

    def test_never_queries_the_plugin_state(self):
        self.run_launcher("--session", "x")
        for call in self.herdr_calls():
            self.assertNotEqual(call[:2], ["plugin", "list"])

    def test_missing_plugin_root_removes_only_this_launcher(self):
        shutil.rmtree(self.plugin_root)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 3)
        self.assertIn("no longer installed", result.stderr)
        self.assertIn("removed stale launcher", result.stderr)
        self.assertFalse(self.launcher.exists())

    def move_plugin_root(self):
        """Reproduce Herdr moving the checkout out of its build directory."""
        moved = self.base / "managed-plugin"
        shutil.move(str(self.plugin_root), str(moved))
        self.env["HSL_TEST_PLUGIN_ROOT"] = str(moved)
        self.env["HSL_TEST_REGISTERED_ROOT"] = str(moved)
        return moved

    def test_follows_the_plugin_root_herdr_registered_after_a_move(self):
        moved = self.move_plugin_root()
        result = self.run_launcher("--session", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.internal_args(), ["--session", "x"])
        self.assertTrue(self.launcher.exists())
        self.assertIn(str(moved), self.launcher.read_text())

    def test_records_the_new_root_so_later_runs_skip_the_lookup(self):
        self.move_plugin_root()
        self.assertEqual(self.run_launcher("--session", "x").returncode, 0)
        (self.base / "herdr.jsonl").unlink(missing_ok=True)
        result = self.run_launcher("--session", "y")
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in self.herdr_calls():
            self.assertNotEqual(call[:2], ["plugin", "list"])

    def test_keeps_the_launcher_when_the_registered_root_is_unusable(self):
        shutil.rmtree(self.plugin_root)
        self.env["HSL_TEST_REGISTERED_ROOT"] = str(self.plugin_root)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 2)
        self.assertIn("reinstall", result.stderr)
        self.assertTrue(self.launcher.exists())

    def test_keeps_the_launcher_when_the_plugin_state_lookup_fails(self):
        # A broken lookup says nothing about whether the plugin is installed,
        # so it must never be read as "uninstalled".
        shutil.rmtree(self.plugin_root)
        result = self.run_launcher(HSL_TEST_PLUGIN_LIST_EXIT="19")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot determine", result.stderr)
        self.assertTrue(self.launcher.exists())

    def test_keeps_the_launcher_when_herdr_cannot_be_reached(self):
        shutil.rmtree(self.plugin_root)
        result = self.run_launcher(HSL_HERDR_BIN=str(self.base / "no-such-herdr"))
        self.assertEqual(result.returncode, 127)
        self.assertIn("herdr is not available", result.stderr)
        self.assertTrue(self.launcher.exists())

    def test_refuses_to_remove_an_unmanaged_launcher(self):
        shutil.rmtree(self.plugin_root)
        self.launcher.write_text(self.launcher.read_text().replace(
            "# herdr-statusline-managed-launcher:v1", "# not-managed"))
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.launcher.exists())

    def test_uninstall_keeps_config_and_removes_the_launcher(self):
        config_dir = self.make_config_dir()
        result = self.run_launcher("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(config_dir.exists())
        self.assertFalse(self.launcher.exists())
        self.assertIn(["plugin", "uninstall", "herdr-statusline"], self.herdr_calls())

    def test_purge_reads_config_dir_before_uninstall_then_deletes_it(self):
        config_dir = self.make_config_dir()
        result = self.run_launcher("uninstall", "--purge")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(config_dir.exists())
        self.assertFalse(self.launcher.exists())
        calls = self.herdr_calls()
        self.assertLess(
            calls.index(["plugin", "config-dir", "herdr-statusline"]),
            calls.index(["plugin", "uninstall", "herdr-statusline"]),
        )

    def test_purge_succeeds_before_the_first_interactive_initialization(self):
        self.assertFalse(self.config_dir.exists())
        result = self.run_launcher("uninstall", "--purge")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.plugin_root.exists())
        self.assertFalse(self.launcher.exists())

    def test_uninstall_failure_preserves_launcher_and_config(self):
        config_dir = self.make_config_dir()
        result = self.run_launcher("uninstall", "--purge", HSL_TEST_UNINSTALL_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(config_dir.exists())
        self.assertTrue(self.launcher.exists())
        self.assertTrue(self.plugin_root.exists())

    def test_unknown_uninstall_option_changes_nothing(self):
        config_dir = self.make_config_dir()
        for extra in (("--force",), ("--purge", "extra")):
            with self.subTest(extra=extra):
                result = self.run_launcher("uninstall", *extra)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage:", result.stderr)
                self.assertTrue(config_dir.exists())
                self.assertTrue(self.launcher.exists())
                self.assertNotIn(
                    ["plugin", "uninstall", "herdr-statusline"], self.herdr_calls()
                )

    def test_reports_partial_failure_if_launcher_changes_during_uninstall(self):
        result = self.run_launcher(
            "uninstall", HSL_TEST_REPLACE_LAUNCHER=str(self.launcher)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.launcher.exists())
        self.assertIn("unmanaged replacement", self.launcher.read_text())
        self.assertFalse(self.plugin_root.exists())

    def test_purge_refuses_a_dangerous_config_directory(self):
        result = self.run_launcher(
            "uninstall", "--purge", HSL_TEST_CONFIG_DIR=str(self.home)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.home.exists())
        self.assertTrue(self.launcher.exists())
        self.assertNotIn(
            ["plugin", "uninstall", "herdr-statusline"], self.herdr_calls()
        )


FAKE_TMUX_E2E = """#!/bin/sh
python3 - "$@" <<'PY'
import os, pathlib, subprocess, sys
args = sys.argv[1:]
command_file = pathlib.Path(os.environ['HSL_TEST_TMUX_COMMAND'])
if 'new-session' in args:
    command_file.write_text(args[-1])
    raise SystemExit(0)
if 'attach-session' in args:
    env = os.environ.copy()
    env['TMUX'] = 'inner-socket'
    raise SystemExit(subprocess.run(command_file.read_text(), shell=True, env=env).returncode)
raise SystemExit(0)
PY
"""

FAKE_HERDR_E2E = """#!/bin/sh
python3 - "$@" <<'PY'
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with pathlib.Path(os.environ['HSL_TEST_HERDR_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['plugin', 'list']:
    state = pathlib.Path(os.environ['HSL_TEST_STATE_FILE']).read_text().strip()
    plugins = [] if state == 'not-installed' else [{
        'plugin_id': 'herdr-statusline',
        'plugin_root': os.environ['HSL_TEST_PLUGIN_ROOT'],
        'enabled': state == 'enabled',
    }]
    # Spaced JSON on purpose: whitespace is legal and must not break parsing.
    print(json.dumps({'id': 'cli:plugin',
                      'result': {'plugins': plugins, 'type': 'plugin_list'}}))
elif args == ['plugin', 'config-dir', 'herdr-statusline']:
    print(os.environ['HSL_TEST_CONFIG_DIR'])
elif args == ['plugin', 'uninstall', 'herdr-statusline']:
    # A real uninstall drops the record as well as the checkout.
    shutil.rmtree(os.environ['HSL_TEST_PLUGIN_ROOT'], ignore_errors=True)
    pathlib.Path(os.environ['HSL_TEST_STATE_FILE']).write_text('not-installed')
else:
    pathlib.Path(os.environ['HSL_TEST_HERDR_RUN']).write_text(json.dumps({
        'args': args,
        'session': os.environ.get('HERDR_SESSION'),
    }))
PY
"""


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_helper()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.home = self.base / "home"
        self.plugin_root = self.base / "plugin"
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir(parents=True)
        self.state_file = self.base / "state"
        self.config_dir = self.home / ".config/herdr/plugins/config/herdr-statusline"
        make_executable(self.fakebin / "cargo", "#!/bin/sh\nexit 0\n")
        make_executable(self.fakebin / "herdr", FAKE_HERDR_E2E)
        make_executable(self.fakebin / "tmux", FAKE_TMUX_E2E)
        self.env = base_env(self.home, self.fakebin)
        self.env.update(
            {
                "HSL_TEST_CARGO_BIN": str(self.fakebin / "cargo"),
                "HSL_TEST_SKIP_BINARY_CHECK": "1",
                "HSL_HERDR_BIN": str(self.fakebin / "herdr"),
                "HSL_TMUX_BIN": str(self.fakebin / "tmux"),
                "HSL_TEST_STATE_FILE": str(self.state_file),
                "HSL_TEST_PLUGIN_ROOT": str(self.plugin_root),
                "HSL_TEST_CONFIG_DIR": str(self.config_dir),
                "HSL_TEST_HERDR_LOG": str(self.base / "herdr.jsonl"),
                "HSL_TEST_HERDR_RUN": str(self.base / "run.json"),
                "HSL_TEST_TMUX_COMMAND": str(self.base / "tmux-command"),
                "TMPDIR": str(self.base / "tmp"),
            }
        )
        (self.base / "tmp").mkdir()
        self.state_file.write_text("enabled")

    def install(self):
        if self.plugin_root.exists():
            shutil.rmtree(self.plugin_root)
        shutil.copytree(
            ROOT,
            self.plugin_root,
            ignore=shutil.ignore_patterns(".git", "target", "__pycache__"),
        )
        helper = self.plugin_root / "target/release/hsl-config"
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "target/release/hsl-config", helper)
        helper.chmod(0o755)
        subprocess.run(
            ["sh", "scripts/build.sh"],
            cwd=self.plugin_root,
            env=self.env,
            text=True,
            check=True,
            capture_output=True,
        )
        self.launcher = self.home / ".local/bin/hsl"

    def install_through_staging(self):
        """Build where Herdr builds — a throwaway checkout it moves afterwards."""
        staging = self.base / "staging-checkout"
        for path in (staging, self.plugin_root):
            if path.exists():
                shutil.rmtree(path)
        shutil.copytree(
            ROOT,
            staging,
            ignore=shutil.ignore_patterns(".git", "target", "__pycache__"),
        )
        helper = staging / "target/release/hsl-config"
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "target/release/hsl-config", helper)
        helper.chmod(0o755)
        subprocess.run(
            ["sh", "scripts/build.sh"],
            cwd=staging,
            env=self.env,
            text=True,
            check=True,
            capture_output=True,
        )
        shutil.move(str(staging), str(self.plugin_root))
        self.launcher = self.home / ".local/bin/hsl"

    def run_hsl(self, *args, without_tmux=False):
        env = self.env.copy()
        if without_tmux:
            env["HSL_TMUX_BIN"] = str(self.base / "absent-tmux")
        return subprocess.run(
            [str(self.launcher), *args], env=env, text=True, capture_output=True
        )

    def test_install_first_run_passthrough_disabled_uninstall_and_purge(self):
        self.install()
        self.assertTrue(self.launcher.is_file())

        interactive = self.run_hsl("--session", "test")
        self.assertEqual(interactive.returncode, 0, interactive.stderr)
        self.assertTrue((self.config_dir / "config.toml").is_file())
        self.assertTrue((self.config_dir / "herdr-info.sh").is_file())

        direct = self.run_hsl("plugin", "list", without_tmux=True)
        self.assertEqual(direct.returncode, 0, direct.stderr)

        self.state_file.write_text("disabled")
        disabled = self.run_hsl("--session", "test", without_tmux=True)
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertTrue(self.launcher.exists())

        self.state_file.write_text("enabled")
        normal = self.run_hsl("uninstall")
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertTrue(self.config_dir.exists())
        self.assertFalse(self.launcher.exists())

        self.install()
        purge = self.run_hsl("uninstall", "--purge")
        self.assertEqual(purge.returncode, 0, purge.stderr)
        self.assertFalse(self.config_dir.exists())
        self.assertFalse(self.launcher.exists())

    def test_survives_a_build_that_runs_in_a_staging_directory(self):
        self.install_through_staging()
        result = self.run_hsl("--session", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.launcher.exists())
        self.assertEqual(
            json.loads((self.base / "run.json").read_text())["args"],
            ["--session", "test"],
        )

    def test_a_bare_run_hands_herdr_the_default_session(self):
        self.install()
        result = self.run_hsl()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((self.base / "run.json").read_text())
        self.assertEqual(record["args"], [])
        self.assertEqual(record["session"], "default")

    def test_stale_launcher_removes_itself_after_a_direct_herdr_uninstall(self):
        self.install()
        subprocess.run(
            [str(self.fakebin / "herdr"), "plugin", "uninstall", "herdr-statusline"],
            env=self.env,
            check=True,
        )
        result = self.run_hsl()
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.launcher.exists())
        self.assertTrue(
            self.config_dir.exists() or not self.config_dir.parent.exists(),
            "a plain uninstall must not delete user config",
        )

    def test_invalid_config_stops_before_starting_anything(self):
        self.install()
        self.run_hsl("--session", "warmup")
        (self.config_dir / "config.toml").write_text("bogus_key = 1\n")
        (self.base / "run.json").unlink(missing_ok=True)
        result = self.run_hsl("--session", "test")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.base / "run.json").exists())

    def test_reinstalling_preserves_edited_user_configuration(self):
        self.install()
        self.run_hsl("--session", "warmup")
        edited_config = (
            'enabled = true\n\n[statusline]\n'
            'status_position = "top"\nstatus_interval = 9\n'
        )
        edited_script = "#!/bin/sh\necho mine\n"
        (self.config_dir / "config.toml").write_text(edited_config)
        (self.config_dir / "herdr-info.sh").write_text(edited_script)
        self.install()
        self.assertTrue(self.launcher.is_file())
        result = self.run_hsl("--session", "again")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.config_dir / "config.toml").read_text(), edited_config)
        self.assertEqual((self.config_dir / "herdr-info.sh").read_text(), edited_script)


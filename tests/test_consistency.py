"""Pin the values that are duplicated across Rust, shell and TOML.

Each test says why its constant is not physically single-sourced, because the
reason differs per constant. Nothing here is a style check: every one of these
duplications has bitten or can bite silently.
"""

import re
import subprocess
import tomllib
import unittest

from tests.helpers import ROOT, MANAGED_MARKER

MANIFEST = ROOT / "herdr-plugin.toml"
CARGO = ROOT / "Cargo.toml"


def load_toml(path):
    with path.open("rb") as stream:
        return tomllib.load(stream)


def extract(path, pattern, groups=1):
    """Return the joined capture groups, failing loudly if the line is gone.

    A plain assertIn would go quiet the day a definition is renamed or
    deleted, which is the failure these tests exist to catch.
    """
    match = re.search(pattern, path.read_text(), re.M)
    assert match, f"{path.relative_to(ROOT)} no longer matches {pattern!r}"
    return "".join(match.group(index + 1) for index in range(groups))


class ConstantAgreementTests(unittest.TestCase):
    def test_plugin_id_agrees_across_the_shipped_code(self):
        # launcher-body.sh has a real bootstrap constraint: root_is_complete()
        # uses the id to *find* the installation, so it cannot read the id from
        # an installation it has not located yet. bin/hsl-internal and
        # src/configdir.rs could read the manifest, but doing so would put file
        # I/O or codegen behind a constant that never changes. Hence a test.
        plugin_id = load_toml(MANIFEST)["id"]
        self.assertEqual(
            extract(ROOT / "src/configdir.rs", r'^pub const PLUGIN_ID: &str = "([^"]+)";'),
            plugin_id,
        )
        self.assertEqual(
            extract(ROOT / "bin/hsl-internal", r"^PLUGIN_ID=(\S+)$"), plugin_id
        )
        self.assertEqual(
            extract(ROOT / "scripts/launcher-body.sh", r"^PLUGIN_ID=(\S+)$"), plugin_id
        )

    def test_version_agrees_with_the_manifest(self):
        # No bootstrap constraint here: cargo requires Cargo.toml's version and
        # Herdr requires herdr-plugin.toml's, so neither file can defer to the
        # other. Nothing reads CARGO_PKG_VERSION, so a release that bumps one
        # and forgets the other is silent without this test.
        self.assertEqual(
            load_toml(CARGO)["package"]["version"], load_toml(MANIFEST)["version"]
        )

    def test_helper_path_agrees_across_the_shipped_code(self):
        # The authority is Cargo.toml's crate name: herdr-plugin.toml declares
        # no artifact path, so it cannot supply this expected value.
        expected = f"target/release/{load_toml(CARGO)['package']['name']}"
        for relative, pattern in (
            ("bin/hsl-internal", r"^HELPER=\$root/(\S+)$"),
            ("scripts/launcher-body.sh", r"^HELPER=\$PLUGIN_ROOT/(\S+)$"),
        ):
            with self.subTest(file=relative):
                self.assertEqual(extract(ROOT / relative, pattern), expected)
        build = (ROOT / "scripts/build.sh").read_text()
        self.assertIn(f"$root/{expected}", build)
        self.assertIn(f'[ -x "$1/{expected}" ]', (ROOT / "scripts/launcher-body.sh").read_text())

    def test_managed_marker_agrees_across_the_shipped_code(self):
        # launcher-body.sh splits the literal on purpose so that its own source
        # never whole-line-matches a marker scan; see the comment there. This
        # test reads the split shape, so changing the shape breaks this test by
        # design.
        self.assertEqual(
            extract(ROOT / "scripts/install-launcher.sh", r"^MARKER='([^']+)'$"),
            MANAGED_MARKER,
        )
        self.assertEqual(
            extract(
                ROOT / "scripts/launcher-body.sh",
                r'expected="([^"]*)""([^"]*)"',
                groups=2,
            ),
            MANAGED_MARKER,
        )


class TagTests(unittest.TestCase):
    """Tags are a release contract: `herdr plugin install --ref vX.Y.Z` builds
    from them, and scripts/build.sh runs `cargo build --release --locked`, so a
    tag pointing at a commit whose Cargo.lock disagrees is uninstallable."""

    EXPECTED = {"v0.1.0": "0622df4", "v0.1.2": "16bd1b7"}

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            text=True, capture_output=True, check=True,
        ).stdout.strip()

    def test_only_the_two_real_versions_are_tagged(self):
        # 0.1.1 never existed: only 0622df4 and eb38ecb ever touched the
        # version, and it went 0.1.0 -> 0.1.2.
        tags = set(self.git("tag").splitlines())
        self.assertEqual(tags, set(self.EXPECTED))

    def test_each_tag_points_at_the_intended_commit(self):
        # Version agreement alone is not enough: 16de6df also declares 0.1.0
        # and also builds, so the expected object id has to be pinned.
        for tag, short in self.EXPECTED.items():
            with self.subTest(tag=tag):
                actual = self.git("rev-parse", f"{tag}^{{commit}}")
                expected = self.git("rev-parse", f"{short}^{{commit}}")
                self.assertEqual(actual, expected)

    def test_each_tag_has_agreeing_versions(self):
        for tag in self.EXPECTED:
            with self.subTest(tag=tag):
                manifest = self.git("show", f"{tag}:herdr-plugin.toml")
                cargo = self.git("show", f"{tag}:Cargo.toml")
                lock = self.git("show", f"{tag}:Cargo.lock")
                version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
                self.assertEqual(
                    version,
                    re.search(r'^version = "([^"]+)"', cargo, re.M).group(1),
                )
                locked = re.search(
                    r'name = "hsl-config"\nversion = "([^"]+)"', lock
                ).group(1)
                self.assertEqual(version, locked)


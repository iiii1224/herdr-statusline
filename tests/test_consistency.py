"""Pin the values that are duplicated across Rust, shell and TOML.

Each test says why its constant is not physically single-sourced, because the
reason differs per constant. Nothing here is a style check: every one of these
duplications has bitten or can bite silently.
"""

import re
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

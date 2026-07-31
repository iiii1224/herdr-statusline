import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, base_env, make_executable

TEMPLATE = ROOT / "scripts/default-herdr-info.sh"

PANE_STYLE = "#[fg=#ffffff,bg=#5a45a5]"
CWD_STYLE = "#[fg=#ffffff,bg=#2b6cb0]"
GIT_STYLE = "#[fg=#ffffff,bg=#2f855a]"

FAKE_HERDR = """#!/bin/sh
if [ "$1" = pane ] && [ "$2" = current ]; then
    printf '%s\\n' "$HSL_TEST_PANE_JSON"
    exit "${HSL_TEST_PANE_EXIT:-0}"
fi
exit 1
"""


def pane_json(pane_id="w2D:p1", cwd=None, foreground_cwd=None):
    """Build one `herdr pane current` reply.

    Keys are emitted in herdr's own alphabetical order, so the template's `sed`
    meets `"cwd"` before `"foreground_cwd"` exactly as it does against the real
    binary. Getting that order wrong would hide a greedy-match bug.
    """
    fields = ['"agent":"claude"']
    if cwd is not None:
        fields.append('"cwd":"%s"' % cwd)
    fields.append('"focused":true')
    if foreground_cwd is not None:
        fields.append('"foreground_cwd":"%s"' % foreground_cwd)
    fields.append('"pane_id":"%s"' % pane_id)
    return (
        '{"id":"cli:pane:current","result":{"pane":{'
        + ",".join(fields)
        + '},"type":"pane_current"}}'
    )


def segments(stdout):
    """Split one rendered status line into an ordered [(style, text)] list.

    Asserting on this instead of the raw string lets a test say which segments
    exist and what they carry, without restating every escape byte.
    """
    parts = re.split(r"(#\[[^\]]*\])", stdout.strip())
    found = []
    for style, text in zip(parts[1::2], parts[2::2]):
        if style == "#[default]":
            break
        found.append((style, text.strip()))
    return found


class HerdrInfoTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        make_executable(self.fakebin / "herdr", FAKE_HERDR)
        self.env = base_env(self.base / "home", self.fakebin)
        # The temporary directory is outside any repository, so a test that
        # wants "not a repository" gets it. GIT_CONFIG_NOSYSTEM keeps
        # /etc/gitconfig from reaching the fixtures.
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.home = pathlib.Path(self.env["HOME"])

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    def make_repo(self, name="repo"):
        """A one-commit repository on `master`.

        `symbolic-ref` rather than `git init -b`, which needs git 2.28, or
        `init.defaultBranch`, which the ambient config could override.
        """
        repo = self.base / name
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q", ".")
        self.git(repo, "symbolic-ref", "HEAD", "refs/heads/master")
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test")
        (repo / "tracked").write_text("one\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "init")
        return repo

    def run_template(self, json_reply=None, **extra_env):
        env = self.env.copy()
        if json_reply is not None:
            env["HSL_TEST_PANE_JSON"] = json_reply
        env.update(extra_env)
        result = subprocess.run(
            ["sh", str(TEMPLATE)], cwd=ROOT, env=env, text=True, capture_output=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_renders_the_pane_the_cwd_and_a_clean_branch(self):
        repo = self.make_repo()
        out = self.run_template(pane_json(foreground_cwd=str(repo)))
        self.assertEqual(
            segments(out),
            [
                (PANE_STYLE, "w2D:p1"),
                (CWD_STYLE, str(repo)),
                (GIT_STYLE, "master ✔"),
            ],
        )

    def test_prefers_the_foreground_cwd_over_the_pane_cwd(self):
        # A pane whose foreground process has cd'd elsewhere must report where
        # the user actually is, not where the shell started.
        background = self.make_repo("background")
        foreground = self.make_repo("foreground")
        self.git(foreground, "checkout", "-q", "-b", "topic")
        out = self.run_template(
            pane_json(cwd=str(background), foreground_cwd=str(foreground))
        )
        self.assertEqual(
            segments(out)[1:],
            [(CWD_STYLE, str(foreground)), (GIT_STYLE, "topic ✔")],
        )

    def test_falls_back_to_the_pane_cwd(self):
        repo = self.make_repo()
        out = self.run_template(pane_json(cwd=str(repo)))
        self.assertEqual(segments(out)[1], (CWD_STYLE, str(repo)))

    def test_renders_a_home_relative_cwd_with_a_tilde(self):
        work = self.home / "work"
        work.mkdir()
        out = self.run_template(pane_json(foreground_cwd=str(work)))
        self.assertEqual(segments(out)[1], (CWD_STYLE, "~/work"))

    def test_renders_the_home_directory_itself_as_a_bare_tilde(self):
        out = self.run_template(pane_json(foreground_cwd=str(self.home)))
        self.assertEqual(segments(out)[1], (CWD_STYLE, "~"))

    def test_does_not_shorten_a_sibling_of_home(self):
        # /home/ida-old must not collapse to ~-old: the prefix counts only on a
        # path component boundary.
        sibling = pathlib.Path(str(self.home) + "-old")
        sibling.mkdir()
        out = self.run_template(pane_json(foreground_cwd=str(sibling)))
        self.assertEqual(segments(out)[1], (CWD_STYLE, str(sibling)))

    def test_reports_every_working_tree_flag(self):
        repo = self.make_repo("flags")
        for name in ("renamed", "deleted", "modified"):
            (repo / name).write_text("one\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "more")

        # The stash has to exist before the other edits, or `git stash` would
        # sweep them up and the remaining flags would never appear.
        (repo / "stashed").write_text("x\n")
        self.git(repo, "add", "stashed")
        self.git(repo, "stash", "-q")

        (repo / "staged").write_text("x\n")
        self.git(repo, "add", "staged")
        (repo / "modified").write_text("two\n")
        self.git(repo, "mv", "renamed", "renamed2")
        (repo / "deleted").unlink()
        (repo / "untracked").write_text("x\n")

        # $ stashed, + staged, ! modified, » renamed, ✘ deleted, ? untracked.
        # No = because nothing is conflicted, and no ⇡⇣ without an upstream.
        self.assertEqual(
            segments(self.run_template(pane_json(foreground_cwd=str(repo))))[2],
            (GIT_STYLE, "master $+!»✘?"),
        )

    def test_reports_ahead_and_behind_counts(self):
        repo = self.make_repo("diverged")
        self.git(repo, "branch", "base")
        self.git(repo, "branch", "--set-upstream-to=base", "master")
        (repo / "tracked").write_text("mine\n")
        self.git(repo, "commit", "-qam", "on master")
        self.git(repo, "checkout", "-q", "base")
        (repo / "tracked").write_text("theirs\n")
        self.git(repo, "commit", "-qam", "on base")
        self.git(repo, "checkout", "-q", "master")
        # A clean tree still carries no ✔ here: the counts already say the tree
        # is not in sync, which is what ✔ would otherwise claim.
        self.assertEqual(
            segments(self.run_template(pane_json(foreground_cwd=str(repo))))[2],
            (GIT_STYLE, "master ⇡1⇣1"),
        )

    def test_shows_a_short_sha_when_head_is_detached(self):
        repo = self.make_repo("detached")
        head = self.git(repo, "rev-parse", "--short", "HEAD").strip()
        self.git(repo, "checkout", "-q", "--detach")
        self.assertEqual(
            segments(self.run_template(pane_json(foreground_cwd=str(repo))))[2],
            (GIT_STYLE, "%s ✔" % head),
        )

    def test_omits_the_git_segment_outside_a_repository(self):
        plain = self.base / "plain"
        plain.mkdir()
        out = self.run_template(pane_json(foreground_cwd=str(plain)))
        self.assertEqual(
            segments(out), [(PANE_STYLE, "w2D:p1"), (CWD_STYLE, str(plain))]
        )

    def test_keeps_the_cwd_segment_when_the_directory_is_gone(self):
        # Knowing the pane sits in a directory that no longer exists is useful;
        # only the git probe is gated on the directory being there.
        gone = self.base / "vanished"
        out = self.run_template(pane_json(foreground_cwd=str(gone)))
        self.assertEqual(
            segments(out), [(PANE_STYLE, "w2D:p1"), (CWD_STYLE, str(gone))]
        )

    def test_reports_the_pane_alone_when_it_has_no_cwd(self):
        out = self.run_template('{"result":{"pane":{"pane_id":"w2D:p1"}}}')
        self.assertEqual(segments(out), [(PANE_STYLE, "w2D:p1")])

    def test_survives_a_failing_herdr(self):
        out = self.run_template(pane_json(foreground_cwd="/"), HSL_TEST_PANE_EXIT="1")
        self.assertEqual(segments(out), [(PANE_STYLE, "no pane")])

    def test_survives_a_missing_herdr(self):
        # An isolated PATH that still finds `sh` and `git` (so the interpreter
        # can start) but has no `herdr` on it.
        empty = self.base / "empty"
        empty.mkdir()
        sh_dir = str(pathlib.Path(shutil.which("sh")).parent)
        out = self.run_template(
            pane_json(foreground_cwd="/"), PATH="%s:%s" % (empty, sh_dir)
        )
        self.assertEqual(segments(out), [(PANE_STYLE, "no pane")])

    def test_survives_a_missing_git(self):
        # A PATH carrying the stub herdr and the tools the template itself
        # calls, but no git. The git segment must vanish, not the line.
        repo = self.make_repo()
        toolbox = self.base / "toolbox"
        toolbox.mkdir()
        for tool in ("sh", "sed", "grep", "cut"):
            (toolbox / tool).symlink_to(shutil.which(tool))
        out = self.run_template(
            pane_json(foreground_cwd=str(repo)),
            PATH="%s:%s" % (self.fakebin, toolbox),
        )
        self.assertEqual(
            segments(out), [(PANE_STYLE, "w2D:p1"), (CWD_STYLE, str(repo))]
        )

    def test_is_a_valid_posix_script(self):
        self.assertEqual(subprocess.run(["sh", "-n", str(TEMPLATE)]).returncode, 0)

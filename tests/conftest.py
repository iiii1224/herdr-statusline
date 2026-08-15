"""Session-wide policy: CI must never go green on a skipped test.

A skip means a precondition vanished -- tmux missing, a version too old --
and that is exactly the failure this suite must not hide. Tests that are
deliberately not part of the default run are *deselected* by marker instead,
which is a different thing from being skipped.
"""

import os


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_herdr: compares the committed CLI snapshot against the real "
        "herdr binary. Deselected by default; select with -m live_herdr.",
    )


def pytest_sessionfinish(session, exitstatus):
    if not os.environ.get("CI"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if skipped and session.exitstatus == 0:
        reporter.write_line(
            f"ERROR: {len(skipped)} test(s) skipped; CI forbids silent skips",
            red=True,
        )
        session.exitstatus = 1

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-08-12

### Added

- Status line mouse clicks, opt-in through `mouse_clicks` in `config.toml`.
  Clicks on a `#[range=user|NAME]` area are dispatched to an `on-click.sh`
  hook that the user or another plugin owns. Needs tmux 3.4 or newer.
- `herdr-info.sh`, a generated helper showing the focused Herdr pane, its
  working directory and its Git state.
- A bundled customization skill, installed into the plugin config directory
  for both `.agents/skills/` and `.claude/skills/`.

### Changed

- `config.toml` now configures tmux status options directly instead of
  running a user-supplied status line script.

## [0.1.0] - 2026-08-01

### Added

- Initial release. Wraps interactive Herdr sessions in a disposable,
  status-line-only tmux session; passes utility commands straight through.
- `hsl` launcher installed to `~/.local/bin`, with `hsl uninstall [--purge]`.

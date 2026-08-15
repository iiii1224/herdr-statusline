---
name: customize-herdr-statusline
description: Customize an installed herdr-statusline by editing its config.toml and optional helper scripts. Use when a user asks to change the hsl status line layout, colors, clock, window list, Herdr pane or Git information, shell-powered segments, or number of status lines.
---

# Customize Herdr Statusline

Turn the user's visual and information preferences into a working status line. Inspect the current configuration, make the change, and validate it; do not send the user away to edit TOML by hand.

## Locate the live configuration

Treat the directory containing this installed skill as the likely config directory: from `.agents/skills/customize-herdr-statusline`, it is three levels up. Use it when it contains `config.toml`. Otherwise run:

```sh
herdr plugin config-dir herdr-statusline
```

Edit the `config.toml` and helper scripts in that directory. Do not edit the repository templates under `scripts/` unless the user explicitly asks to change the plugin's defaults.

If `config.toml` does not exist, explain that one interactive `hsl` launch initializes it, then stop. Do not start an interactive session on the user's behalf.

## Understand the request

Read `config.toml` and every helper script it currently references before changing anything. Preserve unrelated customizations.

When the request is vague, ask one compact question covering:

- what information should appear on the left and right;
- the preferred colors or visual style;
- whether the user wants one line or multiple lines.

When the request is specific enough to implement, proceed without asking the user to restate it.

## Edit the status line

Keep general activation in `enabled` and tmux options under `[statusline]`:

```toml
enabled = true

[statusline]
status_interval = 1
status_left = "..."
status_right = "..."
```

Apply these rules:

- Write tmux option names with underscores. They become hyphens, so `window_status_format` configures `window-status-format`.
- Set only `status`, `status_*`, and `window_status_*` options. Use strings or integers; spell tmux flags as strings such as `"on"` and `"off"`.
- Keep every value on one line. Use tmux format and style syntax directly, including `#[fg=#ffffff,bg=#222222]`, `#{pane_title}`, and `#(...)`.
- Use `$HERDR_SESSION` inside shell commands for the Herdr session name. Do not use tmux `#S`, which is always the disposable session name `hsl`.
- Use `$HERDR_PLUGIN_CONFIG_DIR` for helper paths. Never embed the current absolute config path.
- Increase `status_left_length` or `status_right_length` when adding long content; tmux otherwise truncates it.
- Set `status = 2` through `5` for multiple lines and define additional lines with `status_format_1`, `status_format_2`, and so on. Remember that `status_format_0` replaces the normal left/window/right composition.

Put non-trivial data collection in a small POSIX shell helper in the config directory. Make a new helper owner-executable, handle missing commands or data without noisy stderr, and print exactly one status-line value. Follow the shipped `herdr-info.sh` when Herdr pane, working-directory, or Git state is requested.

## Status line buttons

Clickable areas need `mouse_clicks = true` at the top level of `config.toml`,
next to `enabled`, and tmux 3.4 or newer. Clicks go to `on-click.sh` in the
config directory, which the user or another plugin owns; this skill does not
create it. Turning the feature on costs the terminal its native selection and
middle-click paste, so say so before enabling it for someone.

Mark an area with `#[range=user|NAME]` ... `#[norange]`. `NAME` is at most 15
bytes and reaches the hook as its second argument. tmux dispatches its own
ranges through the same hook, so `window`, `session` and `pane` arrive there
too — with the shipped default format, clicking a window name sends
`window`. Treat those three names as reserved.

Two different layers bound what a name can be. tmux's format parser reads the
status line first, so a `#` in a name has to be written `##`, and a space ends
the style attribute and leaves no range at all. Whatever survives that and
becomes the range name is then carried to the hook as one literal argument,
shell metacharacters included. Keeping names to letters, digits and `_` avoids
the first layer entirely.

Three constraints shape the layout:

- Put user ranges in a `status_format_N` you define yourself. Inside
  `status_left` and `status_right` tmux wraps them in `range=left` and
  `range=right`, which shifts the clickable area one column right of the text.
- The clickable area always extends one column past the last character.
- There is no hover: tmux has no `MouseMove` binding, so a button has to look
  clickable on its own.

## Validate before reporting

Run `sh -n` on every shell helper you changed.

Validate `config.toml` with the plugin's own parser:

```sh
hsl --hsl-check-config
```

It exits 0 when the configuration is valid and 2 with an explanation otherwise.
Pass a path to check a file other than the installed one.

Fix every parse or normalization error. Do not claim visual verification unless a fresh `hsl` session was actually inspected. Tell the user to exit and restart `hsl` to load the new configuration, and summarize the changed segments and files.


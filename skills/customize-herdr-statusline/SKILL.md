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

## Validate before reporting

Run `sh -n` on every shell helper you changed.

Validate `config.toml` with the plugin's own parser. Resolve `plugin_root` from the matching entry in:

```sh
herdr plugin list --plugin herdr-statusline --json
```

Then run:

```sh
"$plugin_root/target/release/hsl-config" load "$config_dir/config.toml" >/dev/null
```

Fix every parse or normalization error. Do not claim visual verification unless a fresh `hsl` session was actually inspected. Tell the user to exit and restart `hsl` to load the new configuration, and summarize the changed segments and files.

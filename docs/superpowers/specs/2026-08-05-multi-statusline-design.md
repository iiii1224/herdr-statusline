# Multi-line status line support

Give `config.toml` a way to say what tmux draws on each line when the status
line is more than one row tall, so that `status = 2` becomes useful instead of
merely legal.

## Problem

`status = 2` already reaches tmux today. `option_value` stringifies integers
and `run-in-tmux` issues `set-option -g status 2`, so the bar grows to two
rows. What the user cannot do is say what the second row contains.

tmux has no per-line `status-left` or `status-right`. Line *n* is drawn from
`status-format[n]`, and that is the only lever. `option_name` rejects
`status-format` outright (`src/config.rs:92`), so every extra line falls back
to tmux's default: `status-format[1]` is a pane list, `status-format[2]` a
session list. The disposable session `hsl` builds has one window and one pane,
so a second line renders roughly `P: 0[120x38]` and a third the session name.
Both are noise.

The gap is therefore not the number of lines. It is the content of the extra
ones.

## Measured tmux behaviour

Established against tmux 3.7b, not read from the manual:

- `status` accepts `on`, `off`, and `2` through `5`. `0` and `1` are
  `unknown value`.
- `status-format` ships with three defaults. `[0]` is the classic bar, the one
  that composes `status-left`, `status-right`, `window-status-format` and the
  window list. `[1]` is a pane list, `[2]` a session list, `[3]` and `[4]` are
  empty.
- Setting `status-format` *without* an index replaces the whole array with a
  single element, silently dropping the `[1]` and `[2]` defaults.
- Setting `status-format[9]` succeeds with rc=0 even though `status` tops out
  at 5. Nothing is drawn and nothing is reported.
- `status-format[01]` is normalised to `[1]`. An index too large to parse is
  rejected as `ambiguous option`.
- A `#(...)` job written directly in `status-format[1]` runs and its output is
  drawn. Verified by attaching a real client to a real server and observing
  both the job's side effect and the rendered line. This is what makes the
  feature worth building.
- No tmux option in the `status-*` or `window-status-*` families ends in a
  digit. All 23 names were enumerated (25 lines, since `status-format` reports
  one per index).

## Design

### Configuration

```toml
[statusline]
status = 2
status_style = "bg=#242424,fg=#dadada"

status_left_length = 60
status_left = "#[fg=colour255,bg=colour241] #h #[default]"
status_right = "%m/%d %H:%M:%S"

# The second line, replacing tmux's default pane list.
status_format_1 = "#[align=left]#($HERDR_PLUGIN_CONFIG_DIR/herdr-info.sh)"
```

`status_format_N` maps to `status-format[N]`. The trailing `_N` is the only
place in the config where an underscore does not simply become a hyphen, and
the enumeration above is why it can be: no real option name ends in a digit, so
the rule cannot capture one by accident.

### Mapping rule

`option_name` changes in exactly two ways:

1. The `status-format` rejection is deleted.
2. After the prefix check, a name that is *entirely* `status-format-` followed
   by one or more digits is rewritten to `status-format[<digits>]`. The match
   is anchored at both ends, so `status-format-1-2` and `status-format-` are
   not rewritten and go to tmux as written, where they fail as unknown options.

The digits are carried through verbatim; nothing is parsed into a number. tmux
normalises `[01]` to `[1]` and rejects an unparsable index itself, so there is
no arithmetic here to overflow.

The prefix check — `status`, `status-*`, `window-status-*` — is untouched, and
it is the whole safety argument. `prefix`, key bindings, `mouse`, hooks,
`destroy-unattached` and `remain-on-exit` remain unreachable, so the disposable
session's invariants still cannot be configured away. `status-format` was
always inside that boundary; what is being removed is an ergonomic veto, not a
safety one. Nor does it grant a new capability: `#(...)` already runs a shell
from `status_left`.

The doc comment at `src/config.rs:77-83` argues the boundary in terms of the
prefixes alone and needs no change; the rationale being deleted lives inside
the rejection's own message. The index rule gets a comment of its own,
recording why a bare suffix match cannot capture a real option name.

### Validation: none beyond what tmux does

Three mistakes are possible and none of them is caught:

| Config | Result |
| --- | --- |
| `status_format = "x"` | Passed through unchanged as `status-format`. tmux collapses the array to one element and its `[1]` and `[2]` defaults disappear. |
| `status_format_9 = "x"` | Becomes `status-format[9]`. tmux returns rc=0 and draws nothing. |
| `status_format_1` with no `status = 2` | Applied to a bar that is one line tall. Nothing is drawn. |
| `status_format_abc` | Becomes `status-format-abc`. tmux reports an unknown option and `run-in-tmux` exits 2. |
| `status = 1` | tmux reports `unknown value: 1` and `run-in-tmux` exits 2. |

This is deliberate. The plugin defers naming and validity to tmux
(`src/config.rs:83`), which keeps it free of an option table to maintain, and
that deference is worth more than catching these three cases. The pitfalls are
documented instead.

### Non-goals

A friendlier per-line abstraction — `[[statusline.line]]` with `left` and
`right` — was considered and rejected. tmux offers exactly one `status-left`,
so hsl would have to synthesise `status-format` strings, replicate
`status-left-length` truncation and the window list, and maintain a second
vocabulary that drifts from tmux's. The plugin's premise is that options map
directly to tmux settings.

Raising the number of status lines costs the herdr pane a row. That is tmux's
arithmetic and is left alone.

## Layers that do not change

`write_protocol`, `bin/hsl-internal` and `run-in-tmux` all treat option names
as opaque text, so the new spelling reaches tmux without touching them.
Confirmed:

- `bin/hsl-internal` reads only line 1 of the protocol file, the `enabled` flag.
- `run-in-tmux:239` routes on `case $name in window-status-*)`, so
  `status-format[1]` correctly takes `-g`; `status-format` is a session option.
- Names and values reach tmux as individual argv elements, so the brackets need
  no quoting.

## Tests

**`src/config.rs` unit tests.** Delete `rejects_status_format`. Add:

- `status_format_1` maps to `status-format[1]`.
- A bare `status_format` passes through as `status-format`.
- The index rule does not fire on names it must not touch:
  `window_status_format` stays `window-status-format`, `status_left_length`
  stays `status-left-length`.
- A verbatim-digits case: `status_format_01` maps to `status-format[01]`,
  proving hsl does not parse the index.

**`tests/test_tmux_runtime.py`.** Keep `test_never_takes_over_the_status_format`
— it runs with no options and still guarantees hsl never sets `status-format`
on its own. Add a test that an explicit `status-format[1]` is applied with
`set-option -g`.

**Real-server test.** Modelled on
`test_a_real_server_applies_options_and_feeds_the_status_job`: set `status 2`
and a `status-format[1]` containing a `#(...)` job, attach a client, and assert
the job ran. The mechanism is already known to work; the test pins it.

## Documentation

**`README.md`.** Line 93 says options map to tmux settings "except
`status-format`" — that exception goes. Add a short section covering the
multi-line setup, and record the pitfalls:

- `status_left`, `status_right`, `status_left_length` and the window list are
  all composed by the *default* `status-format[0]`. Writing `status_format_0`
  replaces that default, and those options stop having any effect.
- `status = 2` on its own leaves the second line showing tmux's default pane
  list, which is near-empty in a single-pane session.
- tmux accepts only `on`, `off` and `2` through `5` for `status`.
- Each extra line takes a row from the herdr pane.

**`scripts/default-config.toml`.** Add a commented example alongside the
existing ones, in the same voice.

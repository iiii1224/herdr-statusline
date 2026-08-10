use serde::de::{MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use std::fmt;
use std::fs;
use std::io::Write;
use std::path::Path;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    /// Opt-in for status-line mouse clicks. Off by default: turning the mouse
    /// on costs the outer terminal its native selection, because tmux then
    /// asks it for 1000/1002/1003/1006 reporting.
    #[serde(default)]
    mouse_clicks: bool,
    #[serde(default)]
    statusline: Statusline,
}

fn default_enabled() -> bool {
    true
}

/// `[statusline]` in the order the user wrote it. tmux folds `status-bg` and
/// `status-fg` into `status-style`, so whichever came last has to win; a map
/// type would sort the keys and silently pick a different winner.
#[derive(Debug, Default)]
struct Statusline(Vec<(String, toml::Value)>);

impl<'de> Deserialize<'de> for Statusline {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct TableVisitor;

        impl<'de> Visitor<'de> for TableVisitor {
            type Value = Statusline;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("a table of tmux status options")
            }

            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Statusline, M::Error> {
                let mut entries = Vec::new();
                while let Some(entry) = map.next_entry::<String, toml::Value>()? {
                    entries.push(entry);
                }
                Ok(Statusline(entries))
            }
        }

        deserializer.deserialize_map(TableVisitor)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct NormalizedConfig {
    pub enabled: bool,
    pub mouse_clicks: bool,
    /// `(tmux option name, value)`, in document order.
    pub options: Vec<(String, String)>,
}

pub fn load(config_file: &Path) -> Result<NormalizedConfig, String> {
    let text = fs::read_to_string(config_file)
        .map_err(|e| format!("cannot read {}: {e}", config_file.display()))?;
    let raw: RawConfig = toml::from_str(&text)
        .map_err(|e| format!("cannot parse {}: {e}", config_file.display()))?;
    normalize(raw)
}

fn normalize(raw: RawConfig) -> Result<NormalizedConfig, String> {
    let mut options = Vec::with_capacity(raw.statusline.0.len());
    for (key, value) in raw.statusline.0 {
        options.push((option_name(&key)?, option_value(&key, value)?));
    }
    Ok(NormalizedConfig {
        enabled: raw.enabled,
        mouse_clicks: raw.mouse_clicks,
        options,
    })
}

/// Map a config key to a tmux option name and bound what may be set.
///
/// The prefixes are the whole safety argument: no option named `status`,
/// `status-*` or `window-status-*` can reach `prefix`, key bindings, `mouse`,
/// hooks, `destroy-unattached` or `remain-on-exit`, so the disposable session's
/// invariants cannot be configured away. Whether a name exists at all is
/// tmux's business, which keeps this free of an option table to maintain.
fn option_name(key: &str) -> Result<String, String> {
    let name = key.replace('_', "-");
    if !(name == "status" || name.starts_with("status-") || name.starts_with("window-status-")) {
        return Err(format!(
            "unknown statusline option {key:?}: only `status`, `status_*` \
             and `window_status_*` can be set"
        ));
    }
    // `status_format_1` is tmux's `status-format[1]`, the format of the second
    // status line; TOML bare keys cannot hold brackets, hence the suffix. A
    // bare suffix rule cannot capture a real option because no name in these
    // two families ends in a digit. The digits are carried over verbatim:
    // tmux normalises `[01]` to `[1]` and rejects an index it cannot parse.
    if let Some(index) = name.strip_prefix("status-format-") {
        if !index.is_empty() && index.bytes().all(|byte| byte.is_ascii_digit()) {
            return Ok(format!("status-format[{index}]"));
        }
    }
    Ok(name)
}

fn option_value(key: &str, value: toml::Value) -> Result<String, String> {
    let text = match value {
        toml::Value::String(text) => text,
        toml::Value::Integer(number) => number.to_string(),
        toml::Value::Boolean(_) => {
            return Err(format!(
                "statusline option {key:?} must be a string or an integer; \
                 tmux spells flags \"on\" and \"off\""
            ))
        }
        other => {
            return Err(format!(
                "statusline option {key:?} must be a string or an integer, not {}",
                other.type_str()
            ))
        }
    };
    if text.contains('\n') || text.contains('\r') {
        return Err(format!("statusline option {key:?} must be a single line"));
    }
    Ok(text)
}

pub fn write_protocol(config: &NormalizedConfig, mut out: impl Write) -> Result<(), String> {
    writeln!(out, "{}", config.enabled).map_err(|e| e.to_string())?;
    writeln!(out, "{}", config.mouse_clicks).map_err(|e| e.to_string())?;
    writeln!(out, "{}", config.options.len()).map_err(|e| e.to_string())?;
    for (name, value) in &config.options {
        writeln!(out, "{name}").map_err(|e| e.to_string())?;
        writeln!(out, "{value}").map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn load_text(text: &str) -> Result<NormalizedConfig, String> {
        let temp = tempdir().unwrap();
        let config_file = temp.path().join("config.toml");
        fs::write(&config_file, text).unwrap();
        load(&config_file)
    }

    fn options(text: &str) -> Vec<(String, String)> {
        load_text(text).unwrap().options
    }

    #[test]
    fn defaults_to_enabled_with_no_options() {
        let config = load_text("").unwrap();
        assert!(config.enabled);
        assert!(config.options.is_empty());
    }

    #[test]
    fn an_empty_section_configures_nothing() {
        assert!(options("[statusline]\n").is_empty());
    }

    #[test]
    fn maps_underscores_to_tmux_option_names() {
        assert_eq!(
            options("[statusline]\nwindow_status_current_format = \" #I \"\n"),
            vec![(
                "window-status-current-format".to_string(),
                " #I ".to_string()
            )]
        );
    }

    #[test]
    fn accepts_the_three_name_forms() {
        assert_eq!(options("[statusline]\nstatus = \"on\"\n")[0].0, "status");
        assert_eq!(
            options("[statusline]\nstatus_justify = \"centre\"\n")[0].0,
            "status-justify"
        );
        assert_eq!(
            options("[statusline]\nwindow_status_format = \"x\"\n")[0].0,
            "window-status-format"
        );
    }

    #[test]
    fn rejects_names_outside_those_forms() {
        for text in [
            "[statusline]\nprefix = \"C-b\"\n",
            "[statusline]\nmouse = \"on\"\n",
            "[statusline]\nstatusbar = \"on\"\n",
            "[statusline]\nwindow_size = \"latest\"\n",
            "[statusline]\ndestroy_unattached = \"off\"\n",
        ] {
            assert!(load_text(text).is_err(), "accepted {text:?}");
        }
    }

    #[test]
    fn indexes_status_format_for_the_extra_status_lines() {
        assert_eq!(
            options("[statusline]\nstatus_format_1 = \"x\"\n")[0].0,
            "status-format[1]"
        );
    }

    #[test]
    fn carries_the_index_over_verbatim() {
        // tmux normalises `[01]` to `[1]` and rejects an index it cannot
        // parse, so nothing here parses one and nothing here can overflow.
        assert_eq!(
            options("[statusline]\nstatus_format_01 = \"x\"\n")[0].0,
            "status-format[01]"
        );
    }

    #[test]
    fn leaves_an_unindexed_status_format_to_tmux() {
        // tmux collapses the array to a single element, dropping its own
        // status-format[1] and [2]. That is tmux's behaviour to own, not ours
        // to veto.
        assert_eq!(
            options("[statusline]\nstatus_format = \"x\"\n")[0].0,
            "status-format"
        );
    }

    #[test]
    fn does_not_index_names_that_only_look_indexed() {
        // No tmux option in these two families ends in a digit, which is what
        // makes a bare suffix rule safe; these are the near misses.
        for (text, expected) in [
            (
                "[statusline]\nwindow_status_format = \"x\"\n",
                "window-status-format",
            ),
            (
                "[statusline]\nstatus_left_length = 20\n",
                "status-left-length",
            ),
            (
                "[statusline]\nstatus_format_1_2 = \"x\"\n",
                "status-format-1-2",
            ),
            ("[statusline]\nstatus_format_ = \"x\"\n", "status-format-"),
            (
                "[statusline]\nstatus_format_abc = \"x\"\n",
                "status-format-abc",
            ),
        ] {
            assert_eq!(options(text)[0].0, expected, "{text:?}");
        }
    }

    #[test]
    fn stringifies_integers_and_rejects_booleans() {
        assert_eq!(options("[statusline]\nstatus_interval = 5\n")[0].1, "5");
        let error = load_text("[statusline]\nstatus = true\n").unwrap_err();
        assert!(error.contains("\"on\""), "{error}");
    }

    #[test]
    fn rejects_line_breaks_and_non_scalar_values() {
        assert!(load_text("[statusline]\nstatus_left = \"a\\nb\"\n").is_err());
        assert!(load_text("[statusline]\nstatus_left = \"a\\rb\"\n").is_err());
        assert!(load_text("[statusline]\nstatus_left = [\"a\"]\n").is_err());
    }

    #[test]
    fn keeps_an_empty_value_so_an_option_can_be_cleared() {
        assert_eq!(options("[statusline]\nstatus_left = \"\"\n")[0].1, "");
    }

    #[test]
    fn preserves_document_order() {
        // tmux folds status-bg into status-style, so whichever the user wrote
        // last must win. The names here are deliberately not alphabetical and
        // deliberately not all real: tmux validates names, this layer does not.
        let text = "[statusline]\nstatus_zebra = 1\nstatus_alpha = 2\nstatus_bg = 3\n";
        let names: Vec<String> = options(text).into_iter().map(|(name, _)| name).collect();
        assert_eq!(names, ["status-zebra", "status-alpha", "status-bg"]);
    }

    #[test]
    fn maps_every_option_in_the_motivating_example() {
        // The config this feature exists to make possible, verbatim from the
        // spec. `status_bg` and `status_fg` are deprecated tmux aliases and are
        // still accepted.
        let text = "\
[statusline]
status_interval = 1
status_justify = \"centre\"
status_bg = \"colour238\"
status_fg = \"colour255\"
status_left_length = 20
status_left = \"#[fg=colour255,bg=colour241]Session: #S #[default]\"
status_right_length = 60
status_right = \"#[fg=colour255,bg=colour241] #h | LA: #(cut -d' ' -f-3 /proc/loadavg) | %m/%d %H:%M:%S#[default]\"
window_status_format = \" #I: #W \"
window_status_current_format = \"#[fg=colour255,bg=colour27,bold] #I: #W #[default]\"
";
        let mapped = options(text);
        let names: Vec<&str> = mapped.iter().map(|(name, _)| name.as_str()).collect();
        assert_eq!(
            names,
            [
                "status-interval",
                "status-justify",
                "status-bg",
                "status-fg",
                "status-left-length",
                "status-left",
                "status-right-length",
                "status-right",
                "window-status-format",
                "window-status-current-format",
            ]
        );
        assert_eq!(mapped[0].1, "1");
        assert_eq!(mapped[4].1, "20");
        assert_eq!(mapped[8].1, " #I: #W ");
        assert!(mapped[7].1.contains("#(cut -d' ' -f-3 /proc/loadavg)"));
        assert!(mapped[7].1.contains("%m/%d %H:%M:%S"));
    }

    #[test]
    fn rejects_unknown_top_level_keys_and_broken_toml() {
        assert!(load_text("extra = true\n").is_err());
        assert!(load_text("interval = 2\n").is_err());
        assert!(load_text("enabled = [true\n").is_err());
        assert!(load_text("enabled = \"yes\"\n").is_err());
    }

    #[test]
    fn defaults_mouse_clicks_to_off_and_parses_both_values() {
        assert!(!load_text("enabled = true\n").unwrap().mouse_clicks);
        assert!(load_text("mouse_clicks = true\n").unwrap().mouse_clicks);
        assert!(!load_text("mouse_clicks = false\n").unwrap().mouse_clicks);
    }

    #[test]
    fn rejects_a_non_boolean_mouse_clicks() {
        assert!(load_text("mouse_clicks = \"on\"\n").is_err());
        assert!(load_text("mouse_clicks = 1\n").is_err());
    }

    #[test]
    fn writes_the_variable_length_protocol() {
        let config = load_text(
            "enabled = false\nmouse_clicks = true\n\
             [statusline]\nstatus_interval = 2\nstatus_left = \" a \"\n",
        )
        .unwrap();
        let mut out = Vec::new();
        write_protocol(&config, &mut out).unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "false\ntrue\n2\nstatus-interval\n2\nstatus-left\n a \n"
        );
    }

    #[test]
    fn reports_a_missing_config_file() {
        let temp = tempdir().unwrap();
        assert!(load(&temp.path().join("absent.toml")).is_err());
    }
}

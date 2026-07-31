use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PluginState {
    Enabled,
    Disabled,
    NotInstalled,
}

#[derive(Deserialize)]
struct Response {
    result: ResultBody,
}

#[derive(Deserialize)]
struct ResultBody {
    plugins: Vec<PluginRecord>,
}

#[derive(Deserialize)]
struct PluginRecord {
    plugin_id: String,
    enabled: bool,
}

/// Parse `herdr plugin list --plugin <id> --json` output.
pub fn parse(input: &str, expected_id: &str) -> Result<PluginState, String> {
    let response: Response = serde_json::from_str(input)
        .map_err(|e| format!("cannot parse Herdr plugin state response: {e}"))?;
    let mut matching = response
        .result
        .plugins
        .iter()
        .filter(|record| record.plugin_id == expected_id);
    let first = match matching.next() {
        Some(record) => record,
        None => return Ok(PluginState::NotInstalled),
    };
    if matching.next().is_some() {
        return Err(format!(
            "Herdr reported more than one record for {expected_id}"
        ));
    }
    Ok(if first.enabled {
        PluginState::Enabled
    } else {
        PluginState::Disabled
    })
}

pub fn as_token(state: PluginState) -> &'static str {
    match state {
        PluginState::Enabled => "enabled",
        PluginState::Disabled => "disabled",
        PluginState::NotInstalled => "not-installed",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ID: &str = "herdr-statusline";

    /// Mirrors the real Herdr 0.7.5 response, including fields we ignore.
    fn response(enabled: bool) -> String {
        format!(
            r#"{{"id":"cli:plugin","result":{{"plugins":[{{"plugin_id":"{ID}","plugin_root":"/p","enabled":{enabled},"name":"herdr-statusline","version":"0.1.0","min_herdr_version":"0.7.5","platforms":["linux"],"source":{{"kind":"github","owner":"iiii1224","repo":"herdr-statusline"}}}}],"type":"plugin_list"}}}}"#
        )
    }

    #[test]
    fn distinguishes_enabled_disabled_and_missing() {
        assert_eq!(parse(&response(true), ID).unwrap(), PluginState::Enabled);
        assert_eq!(parse(&response(false), ID).unwrap(), PluginState::Disabled);
        let empty = r#"{"id":"cli:plugin","result":{"plugins":[],"type":"plugin_list"}}"#;
        assert_eq!(parse(empty, ID).unwrap(), PluginState::NotInstalled);
    }

    #[test]
    fn ignores_records_for_other_plugins() {
        let other = r#"{"result":{"plugins":[{"plugin_id":"herdr-file-viewer","plugin_root":"/x","enabled":true}]}}"#;
        assert_eq!(parse(other, ID).unwrap(), PluginState::NotInstalled);
    }

    #[test]
    fn rejects_invalid_json_and_missing_structure() {
        assert!(parse("not json", ID).is_err());
        assert!(parse("{}", ID).is_err());
        assert!(parse(r#"{"result":{}}"#, ID).is_err());
    }

    #[test]
    fn rejects_duplicate_records() {
        let duplicate = r#"{"result":{"plugins":[
          {"plugin_id":"herdr-statusline","plugin_root":"/a","enabled":true},
          {"plugin_id":"herdr-statusline","plugin_root":"/b","enabled":false}
        ]}}"#;
        assert!(parse(duplicate, ID).is_err());
    }

    #[test]
    fn renders_stable_tokens() {
        assert_eq!(as_token(PluginState::Enabled), "enabled");
        assert_eq!(as_token(PluginState::Disabled), "disabled");
        assert_eq!(as_token(PluginState::NotInstalled), "not-installed");
    }
}

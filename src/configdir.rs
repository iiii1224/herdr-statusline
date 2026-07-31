use std::fs;
use std::path::Path;

pub const PLUGIN_ID: &str = "herdr-statusline";

/// Validate a directory that Herdr reported as this plugin's config directory.
///
/// The path may not exist yet: `init` calls this before creating anything.
/// Symlinked ancestors are allowed so that a symlinked `$HOME` still works;
/// only a symlink at the target itself is rejected.
pub fn validate(dir: &Path) -> Result<(), String> {
    let text = dir
        .to_str()
        .ok_or("plugin config directory is not valid UTF-8")?;
    if text.is_empty() {
        return Err("plugin config directory is empty".into());
    }
    if text.contains('\n') || text.contains('\r') {
        return Err("plugin config directory contains a line break".into());
    }
    if !dir.is_absolute() {
        return Err(format!(
            "plugin config directory is not absolute: {}",
            dir.display()
        ));
    }
    match dir.file_name().and_then(|name| name.to_str()) {
        Some(name) if name == PLUGIN_ID => {}
        _ => {
            return Err(format!(
                "plugin config directory must be named {PLUGIN_ID}: {}",
                dir.display()
            ))
        }
    }
    if let Ok(metadata) = fs::symlink_metadata(dir) {
        if metadata.file_type().is_symlink() {
            return Err(format!(
                "plugin config directory is a symlink: {}",
                dir.display()
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;
    use tempfile::tempdir;

    #[test]
    fn accepts_an_absolute_path_with_the_plugin_basename() {
        let temp = tempdir().unwrap();
        let dir = temp.path().join("herdr/plugins/config/herdr-statusline");
        assert!(validate(&dir).is_ok(), "must accept a not-yet-created dir");
        fs::create_dir_all(&dir).unwrap();
        assert!(validate(&dir).is_ok());
    }

    #[test]
    fn accepts_a_symlinked_ancestor() {
        let temp = tempdir().unwrap();
        let real_home = temp.path().join("real-home");
        let dir = real_home.join("config/herdr-statusline");
        fs::create_dir_all(&dir).unwrap();
        use std::os::unix::fs::symlink;
        let linked_home = temp.path().join("linked-home");
        symlink(&real_home, &linked_home).unwrap();
        let through_link = linked_home.join("config/herdr-statusline");
        assert!(validate(&through_link).is_ok());
    }

    #[test]
    fn rejects_relative_paths_and_line_breaks() {
        assert!(validate(Path::new("relative/herdr-statusline")).is_err());
        assert!(validate(Path::new("/tmp/bad\nname/herdr-statusline")).is_err());
        assert!(validate(Path::new("")).is_err());
    }

    #[test]
    fn rejects_a_wrong_basename() {
        assert!(validate(Path::new("/tmp/plugins/config/other-plugin")).is_err());
        assert!(validate(Path::new("/")).is_err());
    }

    #[test]
    fn rejects_a_symlink_at_the_target_itself() {
        let temp = tempdir().unwrap();
        let real = temp.path().join("real");
        fs::create_dir_all(&real).unwrap();
        let link = temp.path().join("herdr-statusline");
        use std::os::unix::fs::symlink;
        symlink(&real, &link).unwrap();
        assert!(validate(&link).is_err());
    }
}

use crate::configdir;
use std::fs;
use std::io::Write;
use std::path::Path;
use tempfile::NamedTempFile;

const DEFAULT_CONFIG: &str = include_str!("../scripts/default-config.toml");
const DEFAULT_HERDR_INFO: &str = include_str!("../scripts/default-herdr-info.sh");

pub fn initialize(config_dir: &Path) -> Result<(), String> {
    configdir::validate(config_dir)?;
    fs::create_dir_all(config_dir)
        .map_err(|e| format!("cannot create {}: {e}", config_dir.display()))?;
    create_if_missing(config_dir, "config.toml", DEFAULT_CONFIG, 0o600)?;
    create_if_missing(config_dir, "herdr-info.sh", DEFAULT_HERDR_INFO, 0o700)?;
    Ok(())
}

fn create_if_missing(dir: &Path, name: &str, body: &str, mode: u32) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;

    let target = dir.join(name);
    if fs::symlink_metadata(&target).is_ok() {
        return Ok(());
    }
    let mut temp = NamedTempFile::new_in(dir)
        .map_err(|e| format!("cannot stage {}: {e}", target.display()))?;
    temp.write_all(body.as_bytes())
        .map_err(|e| format!("cannot write {}: {e}", target.display()))?;
    temp.flush()
        .map_err(|e| format!("cannot flush {}: {e}", target.display()))?;
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(mode))
        .map_err(|e| format!("cannot set permissions on {}: {e}", target.display()))?;
    temp.as_file()
        .sync_all()
        .map_err(|e| format!("cannot sync {}: {e}", target.display()))?;
    match temp.persist_noclobber(&target) {
        Ok(_) => Ok(()),
        Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(error) => Err(format!(
            "cannot create {}: {}",
            target.display(),
            error.error
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;
    use tempfile::tempdir;

    fn config_dir(temp: &tempfile::TempDir) -> std::path::PathBuf {
        temp.path()
            .join("home/.config/herdr/plugins/config/herdr-statusline")
    }

    #[test]
    fn creates_both_files_and_the_shipped_config_parses() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        initialize(&dir).unwrap();
        assert!(dir.join("herdr-info.sh").is_file());
        assert!(!dir.join("statusline.sh").exists());

        let config = crate::config::load(&dir.join("config.toml")).unwrap();
        assert!(config.enabled);
        assert_eq!(
            config.options,
            vec![
                ("status-interval".to_string(), "1".to_string()),
                ("status-right".to_string(), "%m/%d %H:%M:%S".to_string()),
            ]
        );
    }

    #[test]
    fn ships_the_repository_copy_of_each_template() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        initialize(&dir).unwrap();
        assert_eq!(
            fs::read_to_string(dir.join("config.toml")).unwrap(),
            include_str!("../scripts/default-config.toml")
        );
        assert_eq!(
            fs::read_to_string(dir.join("herdr-info.sh")).unwrap(),
            include_str!("../scripts/default-herdr-info.sh")
        );
    }

    #[test]
    fn creates_only_the_missing_peer() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("config.toml"), "enabled = false\n").unwrap();
        initialize(&dir).unwrap();
        assert_eq!(
            fs::read_to_string(dir.join("config.toml")).unwrap(),
            "enabled = false\n"
        );
        assert!(dir.join("herdr-info.sh").is_file());

        fs::remove_file(dir.join("config.toml")).unwrap();
        fs::write(dir.join("herdr-info.sh"), "#!/bin/sh\necho custom\n").unwrap();
        initialize(&dir).unwrap();
        assert_eq!(
            fs::read_to_string(dir.join("herdr-info.sh")).unwrap(),
            "#!/bin/sh\necho custom\n"
        );
        assert!(dir.join("config.toml").is_file());
    }

    #[test]
    fn leaves_a_legacy_statusline_script_alone() {
        // The script mechanism is gone, but the file is the user's.
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("statusline.sh"), "#!/bin/sh\nexec date\n").unwrap();
        initialize(&dir).unwrap();
        assert_eq!(
            fs::read_to_string(dir.join("statusline.sh")).unwrap(),
            "#!/bin/sh\nexec date\n"
        );
    }

    #[test]
    fn makes_the_script_executable_and_not_world_writable() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        initialize(&dir).unwrap();
        let script_mode = fs::metadata(dir.join("herdr-info.sh"))
            .unwrap()
            .permissions()
            .mode();
        assert_ne!(script_mode & 0o100, 0, "owner execute bit must be set");
        assert_eq!(script_mode & 0o002, 0, "must not be world writable");
        let config_mode = fs::metadata(dir.join("config.toml"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(config_mode & 0o002, 0);
    }

    #[test]
    fn refuses_an_invalid_config_directory() {
        assert!(initialize(Path::new("relative/herdr-statusline")).is_err());
        assert!(initialize(Path::new("/tmp/plugins/config/other-plugin")).is_err());
    }
}

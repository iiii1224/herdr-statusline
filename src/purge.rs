use crate::configdir;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, PartialEq, Eq)]
pub enum PurgeTarget {
    Missing,
    Present(PathBuf),
}

/// Validate a directory before deleting it recursively.
///
/// The caller must source `path` from `herdr plugin config-dir herdr-statusline`
/// rather than building it, so Herdr stays the authority on the location.
pub fn validate(path: &Path, home: &Path) -> Result<PurgeTarget, String> {
    configdir::validate(path)?;
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(PurgeTarget::Missing)
        }
        Err(error) => return Err(format!("cannot inspect {}: {error}", path.display())),
    };
    if metadata.file_type().is_symlink() {
        return Err(format!("refusing to remove a symlink: {}", path.display()));
    }
    if !metadata.is_dir() {
        return Err(format!(
            "refusing to remove a non-directory: {}",
            path.display()
        ));
    }
    let canonical =
        fs::canonicalize(path).map_err(|e| format!("cannot resolve {}: {e}", path.display()))?;
    match canonical.file_name().and_then(|name| name.to_str()) {
        Some(name) if name == configdir::PLUGIN_ID => {}
        _ => {
            return Err(format!(
                "refusing to remove a path that does not resolve to {}: {}",
                configdir::PLUGIN_ID,
                canonical.display()
            ))
        }
    }
    let forbidden = [PathBuf::from("/"), home.to_path_buf(), home.join(".config")];
    for candidate in forbidden {
        if let Ok(resolved) = fs::canonicalize(&candidate) {
            if resolved == canonical {
                return Err(format!("refusing to remove {}", canonical.display()));
            }
        }
    }
    Ok(PurgeTarget::Present(canonical))
}

/// Revalidate immediately before deleting, then remove the directory tree.
pub fn remove(path: &Path, home: &Path) -> Result<(), String> {
    match validate(path, home)? {
        PurgeTarget::Missing => Ok(()),
        PurgeTarget::Present(canonical) => fs::remove_dir_all(&canonical)
            .map_err(|e| format!("cannot remove {}: {e}", canonical.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    fn layout() -> (tempfile::TempDir, std::path::PathBuf, std::path::PathBuf) {
        let temp = tempdir().unwrap();
        let home = temp.path().join("home");
        let target = home.join(".config/herdr/plugins/config/herdr-statusline");
        fs::create_dir_all(&target).unwrap();
        fs::create_dir_all(home.join(".config")).unwrap();
        (temp, home, target)
    }

    #[test]
    fn accepts_a_real_plugin_config_directory() {
        let (_temp, home, target) = layout();
        assert_eq!(
            validate(&target, &home).unwrap(),
            PurgeTarget::Present(target.canonicalize().unwrap())
        );
    }

    #[test]
    fn accepts_a_symlinked_home() {
        let temp = tempdir().unwrap();
        let real_home = temp.path().join("real-home");
        let target = real_home.join(".config/herdr/plugins/config/herdr-statusline");
        fs::create_dir_all(&target).unwrap();
        let linked_home = temp.path().join("home");
        symlink(&real_home, &linked_home).unwrap();
        let through_link = linked_home.join(".config/herdr/plugins/config/herdr-statusline");
        assert!(validate(&through_link, &linked_home).is_ok());
    }

    #[test]
    fn rejects_root_home_and_config_root() {
        let (_temp, home, _target) = layout();
        assert!(validate(Path::new("/"), &home).is_err());
        assert!(validate(&home, &home).is_err());
        assert!(validate(&home.join(".config"), &home).is_err());
    }

    #[test]
    fn rejects_a_wrong_basename() {
        let (_temp, home, _target) = layout();
        let wrong = home.join(".config/herdr/plugins/config/other-plugin");
        fs::create_dir_all(&wrong).unwrap();
        assert!(validate(&wrong, &home).is_err());
    }

    #[test]
    fn rejects_a_symlink_named_like_the_plugin() {
        let (temp, home, _target) = layout();
        let elsewhere = temp.path().join("elsewhere");
        fs::create_dir_all(&elsewhere).unwrap();
        let link = home.join(".config/herdr/plugins/config-alt/herdr-statusline");
        fs::create_dir_all(link.parent().unwrap()).unwrap();
        symlink(&elsewhere, &link).unwrap();
        assert!(
            validate(&link, &home).is_err(),
            "a symlink with the correct basename must still be rejected"
        );
        assert!(elsewhere.exists(), "the symlink target must be untouched");
    }

    #[test]
    fn accepts_a_missing_directory_but_rejects_a_regular_file() {
        let (_temp, home, target) = layout();
        fs::remove_dir_all(&target).unwrap();
        assert_eq!(validate(&target, &home).unwrap(), PurgeTarget::Missing);
        let file = home.join(".config/herdr/plugins/config-file/herdr-statusline");
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(&file, "x").unwrap();
        assert!(validate(&file, &home).is_err());
    }

    #[test]
    fn rejects_a_dangling_symlink_instead_of_treating_it_as_missing() {
        let (_temp, home, target) = layout();
        fs::remove_dir_all(&target).unwrap();
        symlink(home.join("does-not-exist"), &target).unwrap();
        assert!(validate(&target, &home).is_err());
    }

    #[test]
    fn removes_only_the_validated_directory() {
        let (_temp, home, target) = layout();
        fs::write(target.join("config.toml"), "enabled = true\n").unwrap();
        fs::write(target.join("statusline.sh"), "#!/bin/sh\n").unwrap();
        remove(&target, &home).unwrap();
        assert!(!target.exists());
        assert!(target.parent().unwrap().exists());
        assert!(home.exists());
        remove(&target, &home).unwrap();
    }
}

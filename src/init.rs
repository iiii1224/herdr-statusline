use crate::configdir;
use std::fs;
use std::io::Write;
use std::path::Path;
use tempfile::NamedTempFile;

const DEFAULT_CONFIG: &str = include_str!("../scripts/default-config.toml");
const DEFAULT_HERDR_INFO: &str = include_str!("../scripts/default-herdr-info.sh");
const CUSTOMIZE_SKILL: &str = include_str!("../skills/customize-herdr-statusline/SKILL.md");
const CUSTOMIZE_SKILL_OPENAI: &str =
    include_str!("../skills/customize-herdr-statusline/agents/openai.yaml");

pub fn initialize(config_dir: &Path) -> Result<(), String> {
    configdir::validate(config_dir)?;
    fs::create_dir_all(config_dir)
        .map_err(|e| format!("cannot create {}: {e}", config_dir.display()))?;
    create_if_missing(config_dir, "config.toml", DEFAULT_CONFIG, 0o600)?;
    create_if_missing(config_dir, "herdr-info.sh", DEFAULT_HERDR_INFO, 0o700)?;
    let skill_dir = config_dir.join(".agents/skills/customize-herdr-statusline");
    fs::create_dir_all(skill_dir.join("agents"))
        .map_err(|e| format!("cannot create {}: {e}", skill_dir.display()))?;
    create_if_missing(&skill_dir, "SKILL.md", CUSTOMIZE_SKILL, 0o600)?;
    create_if_missing(
        &skill_dir.join("agents"),
        "openai.yaml",
        CUSTOMIZE_SKILL_OPENAI,
        0o600,
    )?;
    let claude_skills = config_dir.join(".claude/skills");
    fs::create_dir_all(&claude_skills)
        .map_err(|e| format!("cannot create {}: {e}", claude_skills.display()))?;
    let claude_skill = claude_skills.join("customize-herdr-statusline");
    create_symlink_if_missing(
        &claude_skill,
        Path::new("../../.agents/skills/customize-herdr-statusline"),
    )?;
    Ok(())
}

fn create_symlink_if_missing(link: &Path, target: &Path) -> Result<(), String> {
    match fs::symlink_metadata(link) {
        Ok(_) => return Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("cannot inspect {}: {error}", link.display())),
    }
    match std::os::unix::fs::symlink(target, link) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(error) => Err(format!("cannot create {}: {error}", link.display())),
    }
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
    fn installs_the_customization_skill_without_overwriting_user_changes() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        initialize(&dir).unwrap();

        let installed_skill = dir.join(".agents/skills/customize-herdr-statusline");
        let shipped_skill =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("skills/customize-herdr-statusline");
        for relative in ["SKILL.md", "agents/openai.yaml"] {
            let installed = installed_skill.join(relative);
            assert!(
                installed.is_file(),
                "the coding-agent skill must be installed"
            );
            assert_eq!(
                fs::read_to_string(installed).unwrap(),
                fs::read_to_string(shipped_skill.join(relative)).unwrap()
            );
        }

        let installed = installed_skill.join("SKILL.md");
        fs::write(&installed, "user-customized skill\n").unwrap();
        initialize(&dir).unwrap();
        assert_eq!(
            fs::read_to_string(installed).unwrap(),
            "user-customized skill\n"
        );
    }

    #[test]
    fn links_the_customization_skill_into_claude_code_discovery() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        initialize(&dir).unwrap();

        let link = dir.join(".claude/skills/customize-herdr-statusline");
        assert!(
            fs::symlink_metadata(&link)
                .unwrap()
                .file_type()
                .is_symlink(),
            "Claude Code must see a skill-directory symlink"
        );
        assert_eq!(
            fs::read_link(&link).unwrap(),
            Path::new("../../.agents/skills/customize-herdr-statusline")
        );
        assert_eq!(
            fs::canonicalize(link).unwrap(),
            fs::canonicalize(dir.join(".agents/skills/customize-herdr-statusline")).unwrap()
        );
    }

    #[test]
    fn preserves_an_existing_claude_code_skill_path() {
        let temp = tempdir().unwrap();
        let dir = config_dir(&temp);
        let existing = dir.join(".claude/skills/customize-herdr-statusline");
        fs::create_dir_all(&existing).unwrap();
        fs::write(existing.join("SKILL.md"), "user-managed Claude skill\n").unwrap();

        initialize(&dir).unwrap();

        assert!(!fs::symlink_metadata(&existing)
            .unwrap()
            .file_type()
            .is_symlink());
        assert_eq!(
            fs::read_to_string(existing.join("SKILL.md")).unwrap(),
            "user-managed Claude skill\n"
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

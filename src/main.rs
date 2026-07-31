mod config;
mod configdir;
mod init;
mod plugin_state;
mod purge;

use std::env;
use std::io::Read;
use std::path::Path;
use std::process::ExitCode;

const USAGE: &str = "usage: hsl-config <plugin-state|init|load|validate-purge|purge-config> ...";

fn run() -> Result<(), String> {
    let args: Vec<String> = env::args().skip(1).collect();
    match args.as_slice() {
        [command] if command == "plugin-state" => {
            let mut input = String::new();
            std::io::stdin()
                .read_to_string(&mut input)
                .map_err(|e| format!("cannot read plugin state from stdin: {e}"))?;
            let state = plugin_state::parse(&input, configdir::PLUGIN_ID)?;
            println!("{}", plugin_state::as_token(state));
            Ok(())
        }
        [command, config_dir] if command == "init" => init::initialize(Path::new(config_dir)),
        [command, config_file] if command == "load" => {
            let normalized = config::load(Path::new(config_file))?;
            config::write_protocol(&normalized, std::io::stdout())
        }
        [command, config_dir, home_dir] if command == "validate-purge" => {
            match purge::validate(Path::new(config_dir), Path::new(home_dir))? {
                purge::PurgeTarget::Missing => println!("missing"),
                purge::PurgeTarget::Present(canonical) => {
                    let text = canonical
                        .to_str()
                        .ok_or("resolved config directory is not valid UTF-8")?;
                    println!("{text}");
                }
            }
            Ok(())
        }
        [command, config_dir, home_dir] if command == "purge-config" => {
            purge::remove(Path::new(config_dir), Path::new(home_dir))
        }
        _ => Err(USAGE.to_string()),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("hsl-config: {message}");
            ExitCode::from(2)
        }
    }
}

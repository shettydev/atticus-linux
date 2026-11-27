//! Linux Contacts & Profile Import (`--contacts`, `--profile-import`) via local vCard / Evolution / Git.

use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;
use std::process::Command;

pub fn parse_vcards(raw: &str) -> Vec<Value> {
    let mut cards = Vec::new();
    let mut in_card = false;
    let (mut name, mut nick, mut org) = (String::new(), String::new(), String::new());
    let (mut emails, mut phones) = (Vec::<String>::new(), Vec::<String>::new());
    for line in raw.lines() {
        let l = line.trim();
        let up = l.to_uppercase();
        if up == "BEGIN:VCARD" {
            in_card = true;
            name.clear(); nick.clear(); org.clear();
            emails.clear(); phones.clear();
        } else if up == "END:VCARD" {
            if in_card && (!name.is_empty() || !emails.is_empty() || !phones.is_empty()) {
                cards.push(json!({
                    "index": cards.len(),
                    "name": name,
                    "nickname": nick,
                    "organization": org,
                    "emails": emails,
                    "phones": phones,
                }));
            }
            in_card = false;
        } else if in_card {
            if let Some((k, v)) = l.split_once(':') {
                let prop = k.split(';').next().unwrap_or("").to_uppercase();
                let val = v.trim().to_string();
                match prop.as_str() {
                    "FN" => name = val,
                    "NICKNAME" => nick = val,
                    "ORG" => org = val.split(';').next().unwrap_or("").to_string(),
                    "EMAIL" if !val.is_empty() => emails.push(val),
                    "TEL" if !val.is_empty() => phones.push(val),
                    _ => {}
                }
            }
        }
    }
    cards
}

pub fn run_contacts_cli() -> i32 {
    let home = std::env::var("HOME").unwrap_or_default();
    let dirs = [
        PathBuf::from(&home).join(".contacts"),
        PathBuf::from(&home).join(".local/share/contacts"),
        PathBuf::from(&home).join(".local/share/kpeoplevcard"),
    ];
    let mut all = Vec::new();
    for dir in dirs {
        if let Ok(entries) = fs::read_dir(dir) {
            for e in entries.flatten() {
                if e.path().extension().and_then(|s| s.to_str()) == Some("vcf") {
                    if let Ok(raw) = fs::read_to_string(e.path()) {
                        all.extend(parse_vcards(&raw));
                    }
                }
            }
        }
    }
    println!("{}", json!({"own": {}, "cards": all}));
    0
}

pub fn run_profile_import_cli() -> i32 {
    let git_val = |k: &str| {
        Command::new("git")
            .args(["config", "--get", k])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default()
    };
    let name = git_val("user.name");
    let email = git_val("user.email");
    if name.is_empty() && email.is_empty() {
        println!("{}", json!({"contacts": null, "mail": null, "errors": ["contacts", "mail"]}));
        return 0;
    }
    let emails = if email.is_empty() {
        vec![]
    } else {
        vec![json!({"label": "personal", "value": email})]
    };
    let mail = if email.is_empty() {
        vec![]
    } else {
        vec![json!({"name": "Local", "emails": [email]})]
    };
    println!("{}", json!({
        "contacts": {
            "name": name, "pronouns": "", "birthday": "",
            "emails": emails, "phones": [], "urls": [], "addresses": [],
            "organization": "", "department": "", "jobTitle": "",
            "social": [], "related": []
        },
        "mail": mail,
        "errors": [],
        "errorReasons": {}
    }));
    0
}

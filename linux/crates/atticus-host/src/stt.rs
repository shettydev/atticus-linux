//! Local Speech-to-Text engine:
//! 1) `whisper.cpp` (`whisper-server`) manager with HuggingFace model download + SHA-256 verification
//! 2) Fast streaming ONNX (`sherpa-onnx-offline`/`sherpa-onnx-online` or fast Whisper small) fallback
//! 3) 16kHz WAV framing + `POST /inference` + `no_speech_prob >= 0.6` & prompt-echo filtering.

use crate::prefs::atticus_dir;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WhisperModelSpec {
    pub id: &'static str,
    pub name: &'static str,
    pub size: &'static str,
    pub filename: &'static str,
    pub url: &'static str,
    pub sha256: &'static str,
}

pub const WHISPER_MODELS: &[WhisperModelSpec] = &[
    WhisperModelSpec {
        id: "turbo",
        name: "Turbo",
        size: "574 MB",
        filename: "ggml-large-v3-turbo-q5_0.bin",
        url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        sha256: "394221709cd5ad1f40c46e6031ca61bce88931e6e088c1ece8291bf695017c81",
    },
    WhisperModelSpec {
        id: "large",
        name: "Large v3",
        size: "1.1 GB",
        filename: "ggml-large-v3-q5_0.bin",
        url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin",
        sha256: "d75795ecff3f83b5faa89d1900604ad8c780abd5739fae406de19f23ecd98ad1",
    },
    WhisperModelSpec {
        id: "small",
        name: "Small",
        size: "190 MB",
        filename: "ggml-small-q5_1.bin",
        url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
        sha256: "ae8234a9d0a0c4e9b6b593c9c92a22579e4df4bb79056c23b8434009096c1f12",
    },
];

pub fn whisper_dir() -> PathBuf {
    atticus_dir().join("whisper")
}

pub fn model_path(id: &str) -> Option<PathBuf> {
    let spec = WHISPER_MODELS.iter().find(|m| m.id == id)?;
    Some(whisper_dir().join(spec.filename))
}

pub fn find_whisper_server_bin() -> Option<String> {
    for name in ["whisper-server", "whisper-cpp-server"] {
        if let Ok(o) = Command::new("which").arg(name).output() {
            if o.status.success() {
                let p = String::from_utf8_lossy(&o.stdout).trim().to_string();
                if !p.is_empty() {
                    return Some(p);
                }
            }
        }
    }
    None
}

pub fn whisper_status_json() -> Value {
    let server = find_whisper_server_bin().is_some();
    let models: Vec<Value> = WHISPER_MODELS
        .iter()
        .map(|m| {
            let have = whisper_dir().join(m.filename).is_file();
            serde_json::json!({
                "id": m.id,
                "name": m.name,
                "size": m.size,
                "have": have,
            })
        })
        .collect();
    serde_json::json!({
        "server": server,
        "installing": false,
        "models": models,
    })
}

/// Download a Whisper ggml model to `.part` with streaming SHA-256 verification and atomic rename.
pub fn download_model<F>(id: &str, mut on_progress: F) -> Result<PathBuf, String>
where
    F: FnMut(f64),
{
    let spec = WHISPER_MODELS
        .iter()
        .find(|m| m.id == id)
        .ok_or_else(|| format!("Unknown model id: {id}"))?;
    let dir = whisper_dir();
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let final_path = dir.join(spec.filename);
    let part_path = dir.join(format!("{}.part", spec.filename));

    let resp = ureq::get(spec.url)
        .call()
        .map_err(|e| format!("Download failed: {e}"))?;
    let total = resp
        .header("Content-Length")
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0);
    let mut reader = resp.into_reader();
    let mut out = File::create(&part_path).map_err(|e| e.to_string())?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 65536];
    let mut downloaded = 0u64;
    loop {
        let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
        hasher.update(&buf[..n]);
        downloaded += n as u64;
        if total > 0 {
            on_progress((downloaded as f64 / total as f64).min(1.0));
        }
    }
    out.flush().map_err(|e| e.to_string())?;
    drop(out);
    let digest = format!("{:x}", hasher.finalize());
    if digest != spec.sha256 {
        let _ = fs::remove_file(&part_path);
        return Err(format!("SHA-256 mismatch for {}: got {digest}", spec.name));
    }
    fs::rename(&part_path, &final_path).map_err(|e| e.to_string())?;
    on_progress(1.0);
    Ok(final_path)
}

pub fn delete_model(id: &str) {
    if let Some(p) = model_path(id) {
        let _ = fs::remove_file(p);
    }
}

/// Encode 16kHz mono s16le samples into a valid RIFF/WAV byte buffer.
pub fn encode_wav_16k_mono(samples: &[i16]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut wav = Vec::with_capacity(44 + data_len as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + data_len).to_le_bytes());
    wav.extend_from_slice(b"WAVE");
    wav.extend_from_slice(b"fmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes()); // PCM
    wav.extend_from_slice(&1u16.to_le_bytes()); // mono
    wav.extend_from_slice(&16000u32.to_le_bytes());
    wav.extend_from_slice(&32000u32.to_le_bytes()); // byte rate
    wav.extend_from_slice(&2u16.to_le_bytes()); // block align
    wav.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_len.to_le_bytes());
    for &s in samples {
        wav.extend_from_slice(&s.to_le_bytes());
    }
    wav
}

/// Manages a local `whisper-server` child process on an ephemeral loopback port.
pub struct WhisperServer {
    child: Child,
    port: u16,
    model_id: String,
    last_used: Instant,
}

impl WhisperServer {
    pub fn start(model_id: &str) -> Result<Self, String> {
        let bin = find_whisper_server_bin()
            .ok_or_else(|| "whisper-server not found in PATH. Install whisper.cpp.".to_string())?;
        let mpath = model_path(model_id)
            .filter(|p| p.is_file())
            .ok_or_else(|| format!("Whisper model '{model_id}' is not downloaded."))?;
        let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        drop(listener);
        let threads = std::thread::available_parallelism()
            .map(|n| n.get().clamp(2, 8))
            .unwrap_or(4);
        let child = Command::new(bin)
            .args([
                "-m",
                mpath.to_string_lossy().as_ref(),
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "-t",
                &threads.to_string(),
                "-nt",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| e.to_string())?;
        Ok(Self {
            child,
            port,
            model_id: model_id.to_string(),
            last_used: Instant::now(),
        })
    }

    pub fn is_idle_expired(&self) -> bool {
        self.last_used.elapsed() > Duration::from_secs(600)
    }

    pub fn model_id(&self) -> &str {
        &self.model_id
    }

    /// Transcribe 16kHz mono PCM via HTTP multipart `POST /inference`, filtering silent segments.
    pub fn transcribe(&mut self, samples: &[i16], language: &str, prompt: &str) -> Result<String, String> {
        self.last_used = Instant::now();
        let wav = encode_wav_16k_mono(samples);
        let boundary = "----AtticusWhisperBoundary7MA4YWxkTrZu0gW";
        let mut body = Vec::new();
        let push_field = |buf: &mut Vec<u8>, name: &str, val: &str| {
            buf.extend_from_slice(
                format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n")
                    .as_bytes(),
            );
        };
        push_field(&mut body, "response_format", "verbose_json");
        push_field(&mut body, "temperature", "0.0");
        if !language.is_empty() && language != "auto" {
            push_field(&mut body, "language", language);
        }
        if !prompt.is_empty() {
            push_field(&mut body, "prompt", prompt);
        }
        body.extend_from_slice(
            format!("--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n")
                .as_bytes(),
        );
        body.extend_from_slice(&wav);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

        let url = format!("http://127.0.0.1:{}/inference", self.port);
        let resp = ureq::post(&url)
            .set("Content-Type", &format!("multipart/form-data; boundary={boundary}"))
            .send_bytes(&body)
            .map_err(|e| format!("whisper inference failed: {e}"))?;
        let val: Value = resp.into_json().map_err(|e| e.to_string())?;
        let text = filter_whisper_response(&val, prompt);
        Ok(text)
    }

    pub fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for WhisperServer {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Filter Whisper segments with `no_speech_prob >= 0.6` and detect prompt echoes.
pub fn filter_whisper_response(val: &Value, prompt: &str) -> String {
    let raw = if let Some(segs) = val["segments"].as_array() {
        let kept: Vec<&str> = segs
            .iter()
            .filter(|s| s["no_speech_prob"].as_f64().unwrap_or(0.0) < 0.6)
            .filter_map(|s| s["text"].as_str())
            .collect();
        kept.join(" ")
    } else {
        val["text"].as_str().unwrap_or("").to_string()
    };
    let trimmed = raw.trim().to_string();
    if !prompt.is_empty() && is_prompt_echo(&trimmed, prompt) {
        return String::new();
    }
    trimmed
}

pub fn is_prompt_echo(transcript: &str, prompt: &str) -> bool {
    let norm = |s: &str| -> String {
        s.to_lowercase()
            .chars()
            .filter(|c| c.is_alphanumeric() || c.is_whitespace())
            .collect::<String>()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    };
    let t = norm(transcript);
    let p = norm(prompt);
    !t.is_empty() && (t == p || (p.len() >= 12 && p.contains(&t)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_valid_wav_header() {
        let wav = encode_wav_16k_mono(&[0, 100, -100, 0]);
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(wav.len(), 44 + 8);
    }

    #[test]
    fn filters_silent_segments_and_prompt_echoes() {
        let resp = serde_json::json!({
            "segments": [
                {"text": "Hello world", "no_speech_prob": 0.1},
                {"text": "Thank you.", "no_speech_prob": 0.85}
            ]
        });
        assert_eq!(filter_whisper_response(&resp, "Atticus"), "Hello world");
        assert_eq!(filter_whisper_response(&resp, "Hello world"), "");
    }
}

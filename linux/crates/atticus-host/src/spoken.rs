//! Spoken text sanitizer & incremental sentence splitter (mirrors Atticus.swift SpeechSentences).

/// Convert Markdown / LaTeX / code / graph answers into clean spoken prose capped at 900 chars.
pub fn read_aloud_text(raw: &str) -> String {
    let mut s = raw.to_string();
    // Replace ```graph ... ``` blocks with spoken placeholder
    while let Some(start) = s.find("```graph") {
        if let Some(end_rel) = s[start + 8..].find("```") {
            let end = start + 8 + end_rel + 3;
            s.replace_range(start..end, " Graph on screen. ");
        } else {
            break;
        }
    }
    // Strip other fenced code blocks
    while let Some(start) = s.find("```") {
        if let Some(end_rel) = s[start + 3..].find("```") {
            let end = start + 3 + end_rel + 3;
            s.replace_range(start..end, " ");
        } else {
            s.truncate(start);
            break;
        }
    }
    // Strip inline code backticks
    let mut out = String::with_capacity(s.len());
    let mut in_code = false;
    for ch in s.chars() {
        if ch == '`' {
            in_code = !in_code;
            continue;
        }
        if !in_code {
            out.push(ch);
        }
    }
    // Simple LaTeX & symbol verbalization
    let cleaned = out
        .replace("$$", " ")
        .replace('$', " ")
        .replace("\\times", " times ")
        .replace("\\div", " divided by ")
        .replace("\\le", " less than or equal to ")
        .replace("\\ge", " greater than or equal to ")
        .replace("\\neq", " not equal to ")
        .replace("\\sqrt", " square root of ")
        .replace("\\pi", " pi ")
        .replace("^2", " squared")
        .replace("^3", " cubed")
        .replace('≤', " less than or equal to ")
        .replace('≥', " greater than or equal to ")
        .replace('×', " times ")
        .replace("**", "")
        .replace("__", "")
        .replace("##", "")
        .replace('#', "");

    // Strip URLs
    let words: Vec<&str> = cleaned
        .split_whitespace()
        .filter(|w| !w.starts_with("http://") && !w.starts_with("https://"))
        .collect();
    let joined = words.join(" ");
    if joined.chars().count() > 900 {
        let mut clipped: String = joined.chars().take(880).collect();
        if let Some(idx) = clipped.rfind(|c| c == '.' || c == '!' || c == '?') {
            clipped.truncate(idx + 1);
        }
        format!("{clipped} The rest is on screen.")
    } else {
        joined
    }
}

/// Incremental sentence splitter for streaming TTS playback.
#[derive(Default)]
pub struct SpeechSentences {
    buffer: String,
    emitted: usize,
}

impl SpeechSentences {
    pub fn push(&mut self, full_or_delta: &str, is_full: bool) -> Vec<String> {
        if is_full {
            self.buffer = full_or_delta.to_string();
        } else {
            self.buffer.push_str(full_or_delta);
        }
        let mut ready = Vec::new();
        let slice = &self.buffer[self.emitted..];
        let mut last_cut = 0;
        let bytes = slice.as_bytes();
        let mut i = 0;
        while i < bytes.len() {
            let ch = slice[i..].chars().next().unwrap();
            let clen = ch.len_utf8();
            let is_end = matches!(ch, '.' | '!' | '?' | '。' | '！' | '？' | '\n');
            if is_end {
                let next_i = i + clen;
                let followed_by_space = next_i >= bytes.len()
                    || slice[next_i..].chars().next().map(|c| c.is_whitespace()).unwrap_or(true);
                if followed_by_space && next_i > last_cut + 2 {
                    let seg = read_aloud_text(&slice[last_cut..next_i]);
                    if !seg.is_empty() {
                        ready.push(seg);
                    }
                    last_cut = next_i;
                }
            }
            i += clen;
        }
        self.emitted += last_cut;
        ready
    }

    pub fn flush(&mut self) -> Option<String> {
        if self.emitted >= self.buffer.len() {
            return None;
        }
        let tail = read_aloud_text(&self.buffer[self.emitted..]);
        self.emitted = self.buffer.len();
        if tail.is_empty() {
            None
        } else {
            Some(tail)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitizes_graph_code_and_math() {
        let out = read_aloud_text("Here is $x^2 \\times 3$. ```graph {\"fn\":[\"x^2\"]}``` See https://example.com");
        assert!(out.contains("x squared times 3"));
        assert!(out.contains("Graph on screen."));
        assert!(!out.contains("https://"));
    }

    #[test]
    fn streams_sentences_incrementally() {
        let mut s = SpeechSentences::default();
        assert_eq!(s.push("Hello world. How are", true), vec!["Hello world."]);
        assert_eq!(
            s.push("Hello world. How are you? Great", true),
            vec!["How are you?"]
        );
        assert_eq!(s.flush(), Some("Great".into()));
    }
}

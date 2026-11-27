//! Screen Drawing, TalkPen cursor trail, Point-at-Screen, Show-me-where Ring/Arrow,
//! and Agent Orb Flight primitives.
//!
//! Includes deterministic splitmix64 hand-drawn jitter (`Hand`) and bounding-box hit geometry
//! used to construct selective click-through `cairo::Region` input masks so clicks pass through
//! everywhere except on drawn marks!

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarkRect {
    pub id: String,
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub label: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrailSample {
    pub x: f64,
    pub y: f64,
    pub t: f64,
}

/// Deterministic splitmix64 PRNG matching Atticus.swift's hand-drawn wobble generator.
pub struct HandPrng {
    state: u64,
}

impl HandPrng {
    pub fn new(seed: u64) -> Self {
        Self { state: seed }
    }

    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9e3779b97f4a7c15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d049bb133111eb);
        z ^ (z >> 31)
    }

    /// Returns a deterministic jitter value in `[-amp, +amp]`.
    pub fn jitter(&mut self, amp: f64) -> f64 {
        let u = (self.next_u64() as f64) / (u64::MAX as f64);
        (u * 2.0 - 1.0) * amp
    }
}

/// Parse a `✎ <json>` drawing payload into clickable mark bounding boxes so the overlay window
/// can install a selective `set_input_region` mask (click-through everywhere except marks).
pub fn extract_mark_hitboxes(drawing: &Value) -> Vec<MarkRect> {
    let mut rects = Vec::new();
    let Some(steps) = drawing["steps"].as_array() else {
        return rects;
    };
    for (si, step) in steps.iter().enumerate() {
        let Some(shapes) = step["shapes"].as_array() else {
            continue;
        };
        for (ki, sh) in shapes.iter().enumerate() {
            let kind = sh["kind"].as_str().unwrap_or("");
            let id = format!("{si}:{ki}");
            let label = sh["text"]
                .as_str()
                .or_else(|| sh["label"].as_str())
                .unwrap_or(kind)
                .to_string();
            let rect = match kind {
                "rect" | "text" => {
                    let x = sh["x"].as_f64().unwrap_or(0.0);
                    let y = sh["y"].as_f64().unwrap_or(0.0);
                    let w = sh["w"].as_f64().unwrap_or(120.0);
                    let h = sh["h"].as_f64().unwrap_or(40.0);
                    Some((x - 8.0, y - 8.0, w + 16.0, h + 16.0))
                }
                "circle" | "pointer" => {
                    let cx = sh["x"].as_f64().unwrap_or(0.0);
                    let cy = sh["y"].as_f64().unwrap_or(0.0);
                    let r = sh["r"].as_f64().unwrap_or(24.0).max(16.0);
                    Some((cx - r - 6.0, cy - r - 6.0, (r + 6.0) * 2.0, (r + 6.0) * 2.0))
                }
                "arrow" | "line" => {
                    let x1 = sh["x1"].as_f64().unwrap_or(0.0);
                    let y1 = sh["y1"].as_f64().unwrap_or(0.0);
                    let x2 = sh["x2"].as_f64().unwrap_or(x1);
                    let y2 = sh["y2"].as_f64().unwrap_or(y1);
                    let minx = x1.min(x2) - 10.0;
                    let miny = y1.min(y2) - 10.0;
                    let w = (x1 - x2).abs() + 20.0;
                    let h = (y1 - y2).abs() + 20.0;
                    Some((minx, miny, w, h))
                }
                "label" => {
                    let x = sh["x"].as_f64().unwrap_or(0.0);
                    let y = sh["y"].as_f64().unwrap_or(0.0);
                    let w = (label.len() as f64 * 8.5).clamp(64.0, 320.0);
                    Some((x - 10.0, y - 16.0, w + 20.0, 36.0))
                }
                _ => None,
            };
            if let Some((x, y, w, h)) = rect {
                rects.push(MarkRect { id, x, y, w, h, label });
            }
        }
    }
    rects
}

/// Evaluate a cubic Bézier curve point for the 6-orb Agent Shelf flight animation (`fly()`).
pub fn cubic_bezier(p0: (f64, f64), p1: (f64, f64), p2: (f64, f64), p3: (f64, f64), t: f64) -> (f64, f64) {
    let u = 1.0 - t;
    let tt = t * t;
    let uu = u * u;
    let uuu = uu * u;
    let ttt = tt * t;
    let x = uuu * p0.0 + 3.0 * uu * t * p1.0 + 3.0 * u * tt * p2.0 + ttt * p3.0;
    let y = uuu * p0.1 + 3.0 * uu * t * p1.1 + 3.0 * u * tt * p2.1 + ttt * p3.1;
    (x, y)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_clickable_mark_regions() {
        let drawing = serde_json::json!({
            "steps": [{
                "shapes": [
                    {"kind": "rect", "x": 100, "y": 200, "w": 80, "h": 40, "label": "Save"},
                    {"kind": "circle", "x": 300, "y": 300, "r": 20}
                ]
            }]
        });
        let hits = extract_mark_hitboxes(&drawing);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].label, "Save");
        assert!(hits[1].w >= 52.0);
    }

    #[test]
    fn bezier_endpoints_match() {
        assert_eq!(cubic_bezier((0.0, 0.0), (10.0, 50.0), (90.0, 50.0), (100.0, 100.0), 0.0), (0.0, 0.0));
        assert_eq!(cubic_bezier((0.0, 0.0), (10.0, 50.0), (90.0, 50.0), (100.0, 100.0), 1.0), (100.0, 100.0));
    }
}

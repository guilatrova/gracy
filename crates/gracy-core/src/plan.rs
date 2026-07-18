//! Serde mirror of the `scheduler_plan` JSON emitted by `python/gracy/plan.py`.
//!
//! This is the FFI contract: `compile_plan()` on the Python side produces a
//! JSON-able dict, and `Plan::from_json` must parse exactly that shape.
//! Defaults mirror the `.get(...)` defaults used by `scheduler_py.PyScheduler`.

use serde::Deserialize;

/// Top-level scheduler plan (throttle + concurrency + queue sections).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Plan {
    #[serde(default)]
    pub throttle: ThrottlePlan,
    #[serde(default)]
    pub concurrency: Vec<ConcRulePlan>,
    #[serde(default)]
    pub queue: QueuePlan,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ThrottlePlan {
    /// "exact" | "smooth" (the reference treats smooth exactly like exact).
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub rules: Vec<ThrottleRulePlan>,
}

impl Default for ThrottlePlan {
    fn default() -> Self {
        Self { mode: default_mode(), rules: Vec::new() }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ThrottleRulePlan {
    pub id: u32,
    /// Regex matched against the FORMATTED url.
    #[serde(rename = "match")]
    pub match_regex: String,
    pub limit: u32,
    /// Window length in seconds.
    pub per: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConcRulePlan {
    pub id: u32,
    /// Regex matched against the UNFORMATTED url (uurl); None = all requests.
    #[serde(rename = "match", default)]
    pub match_regex: Option<String>,
    pub limit: u32,
    pub per_uurl: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct QueuePlan {
    /// Global in-flight cap — becomes an implicit global concurrency rule.
    #[serde(default)]
    pub max_at_once: Option<u32>,
    #[serde(default = "default_max_pending")]
    pub max_pending: u32,
    /// "wait" | "raise"
    #[serde(default = "default_on_full")]
    pub on_full: String,
    #[serde(default)]
    pub throttle_in_hooks: bool,
}

impl Default for QueuePlan {
    fn default() -> Self {
        Self {
            max_at_once: None,
            max_pending: default_max_pending(),
            on_full: default_on_full(),
            throttle_in_hooks: false,
        }
    }
}

fn default_mode() -> String {
    "exact".to_owned()
}

fn default_max_pending() -> u32 {
    10_000
}

fn default_on_full() -> String {
    "wait".to_owned()
}

impl Plan {
    pub fn from_json(json: &str) -> Result<Plan, String> {
        serde_json::from_str(json).map_err(|e| format!("invalid scheduler plan JSON: {e}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Byte-for-byte output of `gracy.plan.compile_plan(...)` on the Python
    /// side for a client with Throttle(Rate(limit=5, per=1.0, match=".*")),
    /// Concurrency(limit=10) and Queue(max_at_once=20, max_pending=100).
    const PYTHON_EMITTED_PLAN: &str = r#"{"throttle": {"mode": "exact", "rules": [{"id": 0, "match": ".*", "limit": 5, "per": 1.0}]}, "concurrency": [{"id": 0, "match": null, "limit": 10, "per_uurl": false}], "queue": {"max_at_once": 20, "max_pending": 100, "on_full": "wait", "throttle_in_hooks": false}}"#;

    #[test]
    fn parses_python_emitted_plan() {
        let plan = Plan::from_json(PYTHON_EMITTED_PLAN).expect("plan must parse");
        assert_eq!(plan.throttle.mode, "exact");
        assert_eq!(plan.throttle.rules.len(), 1);
        let rule = &plan.throttle.rules[0];
        assert_eq!(rule.id, 0);
        assert_eq!(rule.match_regex, ".*");
        assert_eq!(rule.limit, 5);
        assert_eq!(rule.per, 1.0);

        assert_eq!(plan.concurrency.len(), 1);
        let conc = &plan.concurrency[0];
        assert_eq!(conc.id, 0);
        assert_eq!(conc.match_regex, None);
        assert_eq!(conc.limit, 10);
        assert!(!conc.per_uurl);

        assert_eq!(plan.queue.max_at_once, Some(20));
        assert_eq!(plan.queue.max_pending, 100);
        assert_eq!(plan.queue.on_full, "wait");
        assert!(!plan.queue.throttle_in_hooks);
    }

    #[test]
    fn defaults_match_pyscheduler_gets() {
        // PyScheduler uses .get() defaults: mode="exact", max_pending=10_000,
        // on_full="wait", throttle_in_hooks=False, missing sections empty.
        let plan = Plan::from_json("{}").expect("empty plan must parse");
        assert_eq!(plan.throttle.mode, "exact");
        assert!(plan.throttle.rules.is_empty());
        assert!(plan.concurrency.is_empty());
        assert_eq!(plan.queue.max_at_once, None);
        assert_eq!(plan.queue.max_pending, 10_000);
        assert_eq!(plan.queue.on_full, "wait");
        assert!(!plan.queue.throttle_in_hooks);
    }

    #[test]
    fn rejects_invalid_json() {
        assert!(Plan::from_json("not json").is_err());
        assert!(Plan::from_json(r#"{"throttle": {"rules": [{"id": 0}]}}"#).is_err());
    }
}

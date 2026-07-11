//! Keyed concurrency semaphores, created on demand.
//!
//! Mirrors `scheduler_py._ConcurrencyRule` + `_semaphore_for`: one
//! `tokio::sync::Semaphore` per `(rule_id, scope, conc_extra)` where scope is
//! the uurl when the rule is `per_uurl`, else `"global"`.

use std::sync::Arc;

use dashmap::DashMap;
use tokio::sync::Semaphore;

pub const GLOBAL_SCOPE: &str = "global";

/// `queue.max_at_once` becomes an implicit GLOBAL rule with id -1 so it sorts
/// (and is acquired) FIRST, ahead of every user rule — same as the reference.
pub const MAX_AT_ONCE_RULE_ID: i64 = -1;

/// (rule_id, scope: uurl-or-"global", conc_extra)
pub type ConcKey = (i64, String, String);

/// One compiled concurrency rule. `regex: None` matches every request.
#[derive(Debug)]
pub struct CompiledConcRule {
    pub id: i64,
    /// Matched (search semantics) against the UNFORMATTED url (uurl).
    pub regex: Option<regex::Regex>,
    pub limit: u32,
    pub per_uurl: bool,
}

impl CompiledConcRule {
    pub fn matches(&self, uurl: &str) -> bool {
        match &self.regex {
            Some(re) => re.is_match(uurl),
            None => true,
        }
    }
}

/// On-demand keyed semaphores (uurl x conc_extra cardinality).
#[derive(Debug, Default)]
pub struct ConcurrencyMap {
    map: DashMap<ConcKey, Arc<Semaphore>>,
}

impl ConcurrencyMap {
    /// Get (or lazily create with `rule.limit` permits) the semaphore for
    /// this rule/uurl/extra combination.
    pub fn semaphore_for(
        &self,
        rule: &CompiledConcRule,
        uurl: &str,
        conc_extra: &str,
    ) -> Arc<Semaphore> {
        let scope = if rule.per_uurl { uurl } else { GLOBAL_SCOPE };
        self.map
            .entry((rule.id, scope.to_owned(), conc_extra.to_owned()))
            .or_insert_with(|| Arc::new(Semaphore::new(rule.limit as usize)))
            .clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rule(id: i64, per_uurl: bool, limit: u32) -> CompiledConcRule {
        CompiledConcRule { id, regex: None, limit, per_uurl }
    }

    #[test]
    fn per_uurl_rules_get_distinct_semaphores() {
        let map = ConcurrencyMap::default();
        let r = rule(0, true, 1);
        let a = map.semaphore_for(&r, "https://api/a", "");
        let b = map.semaphore_for(&r, "https://api/b", "");
        let a2 = map.semaphore_for(&r, "https://api/a", "");
        assert!(!Arc::ptr_eq(&a, &b));
        assert!(Arc::ptr_eq(&a, &a2));
    }

    #[test]
    fn global_rule_shares_one_semaphore_but_extra_partitions() {
        let map = ConcurrencyMap::default();
        let r = rule(MAX_AT_ONCE_RULE_ID, false, 3);
        let a = map.semaphore_for(&r, "https://api/a", "");
        let b = map.semaphore_for(&r, "https://api/b", "");
        assert!(Arc::ptr_eq(&a, &b));
        let c = map.semaphore_for(&r, "https://api/a", "tenant-1");
        assert!(!Arc::ptr_eq(&a, &c));
    }

    #[test]
    fn none_regex_matches_everything() {
        let r = rule(0, false, 1);
        assert!(r.matches("anything"));
        let scoped = CompiledConcRule {
            id: 1,
            regex: Some(regex::Regex::new("pokemon").expect("static regex")),
            limit: 1,
            per_uurl: false,
        };
        assert!(scoped.matches("https://api/pokemon/{NAME}"));
        assert!(!scoped.matches("https://api/berry/{NAME}"));
    }
}

//! Scheduler semantics tests — virtual time (`start_paused`) makes every
//! timing assertion exact. Each test mirrors an observable behavior of the
//! Python reference scheduler (`python/gracy/scheduler_py.py`).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use gracy_core::plan::Plan;
use gracy_core::queue::{Scheduler, SubmitError};
use tokio::time::Instant;

fn sched(plan_json: &str) -> Scheduler {
    Scheduler::from_plan_json(plan_json).expect("test plan compiles")
}

/// Park the current (main) test task enough times for every spawned task to
/// run up to its next await point — without advancing the virtual clock.
async fn settle() {
    for _ in 0..20 {
        tokio::task::yield_now().await;
    }
}

const MS: Duration = Duration::from_millis(1);

// ------------------------------------------------------------------- 1. plan

#[test]
fn plan_parses_docstring_shape() {
    // The exact shape documented in python/gracy/plan.py's module docstring.
    let json = r#"{
      "throttle": {
        "mode": "exact",
        "rules": [
          {"id": 0, "match": ".*/pokemon/.*", "limit": 10, "per": 1.0},
          {"id": 1, "match": ".*", "limit": 600, "per": 60.0}
        ]
      },
      "concurrency": [
        {"id": 0, "match": null, "limit": 2, "per_uurl": false},
        {"id": 1, "match": "https://api/berry/.*", "limit": 1, "per_uurl": true}
      ],
      "queue": {"max_at_once": 10, "max_pending": 5000, "on_full": "wait",
                "throttle_in_hooks": false}
    }"#;
    let plan = Plan::from_json(json).expect("docstring-shaped plan parses");
    assert_eq!(plan.throttle.mode, "exact");
    assert_eq!(plan.throttle.rules.len(), 2);
    assert_eq!(plan.throttle.rules[1].limit, 600);
    assert_eq!(plan.throttle.rules[1].per, 60.0);
    assert_eq!(plan.concurrency.len(), 2);
    assert_eq!(plan.concurrency[0].match_regex, None);
    assert!(plan.concurrency[1].per_uurl);
    assert_eq!(plan.queue.max_at_once, Some(10));
    assert_eq!(plan.queue.max_pending, 5000);
    assert_eq!(plan.queue.on_full, "wait");

    Scheduler::new(&plan).expect("plan compiles into a scheduler");
}

// --------------------------------------------------------------- 2. sliding window

#[tokio::test(start_paused = true)]
async fn sliding_window_never_exceeds_limit_in_any_trailing_window() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
            [{"id": 0, "match": ".*", "limit": 3, "per": 0.3}]}}"#,
    );
    let grants: Arc<Mutex<Vec<Instant>>> = Arc::new(Mutex::new(Vec::new()));
    let start = Instant::now();

    let mut handles = Vec::new();
    for _ in 0..10 {
        let s = s.clone();
        let grants = grants.clone();
        handles.push(tokio::spawn(async move {
            let g = s.submit("u", "https://api/u", 0, false, false, "").await;
            assert!(g.is_ok());
            grants.lock().unwrap().push(Instant::now());
        }));
    }
    for h in handles {
        h.await.unwrap();
    }

    let mut instants = grants.lock().unwrap().clone();
    instants.sort();
    assert_eq!(instants.len(), 10);
    // Property: no trailing 300ms window ever contains more than 3 grants.
    for w in instants.windows(4) {
        assert!(
            w[3] - w[0] >= 300 * MS,
            "4 grants inside one 300ms window: {:?}",
            w.iter().map(|t| *t - start).collect::<Vec<_>>()
        );
    }
    // Progress: 3 per 300ms means the 10th grant lands at exactly t=900ms.
    assert_eq!(*instants.last().unwrap() - start, 900 * MS);
}

// --------------------------------------------------------------- 3. burst spacing

#[tokio::test(start_paused = true)]
async fn burst_over_limit_grants_strictly_spaced() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
            [{"id": 0, "match": ".*", "limit": 1, "per": 0.1}]}}"#,
    );
    let start = Instant::now();
    let mut instants = Vec::new();
    let mut handles = Vec::new();
    let grants: Arc<Mutex<Vec<Instant>>> = Arc::new(Mutex::new(Vec::new()));
    for _ in 0..5 {
        let s = s.clone();
        let grants = grants.clone();
        handles.push(tokio::spawn(async move {
            s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
            grants.lock().unwrap().push(Instant::now());
        }));
    }
    for h in handles {
        h.await.unwrap();
    }
    instants.extend(grants.lock().unwrap().iter().copied());
    instants.sort();

    for pair in instants.windows(2) {
        assert!(pair[1] - pair[0] >= 100 * MS, "grants not spaced by the window");
    }
    assert_eq!(*instants.last().unwrap() - start, 400 * MS);
}

// --------------------------------------------------------------- 4. overlapping rules

#[tokio::test(start_paused = true)]
async fn two_overlapping_rules_are_both_enforced() {
    // rule 0: 2 per 1s over everything; rule 1: 1 per 300ms over /special/.
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules": [
            {"id": 0, "match": ".*", "limit": 2, "per": 1.0},
            {"id": 1, "match": "special", "limit": 1, "per": 0.3}]}}"#,
    );
    let start = Instant::now();
    let mut offsets = Vec::new();
    for _ in 0..4 {
        let g = s.submit("u", "https://api/special/x", 0, false, false, "").await;
        assert!(g.is_ok());
        offsets.push(Instant::now() - start);
    }
    // t0 both reserve; t300 rule1 frees (rule0 has 1 slot left); then rule0's
    // window binds: t1000; then both align at t1300.
    assert_eq!(offsets, vec![Duration::ZERO, 300 * MS, 1000 * MS, 1300 * MS]);

    let stats = s.stats();
    assert!(stats.throttle_hits.get(&0).copied().unwrap_or(0) >= 1, "rule 0 must record hits");
    assert!(stats.throttle_hits.get(&1).copied().unwrap_or(0) >= 1, "rule 1 must record hits");
    assert_eq!(stats.throttled_by_uurl.get("u").copied(), Some(3));
}

// --------------------------------------------------------------- 5. concurrency

#[tokio::test(start_paused = true)]
async fn per_uurl_concurrency_isolates_lanes() {
    let s = sched(r#"{"concurrency": [{"id": 0, "match": null, "limit": 1, "per_uurl": true}]}"#);

    let g_a = s.submit("A", "https://api/A", 0, false, false, "").await.unwrap();

    // Second submit on uurl A blocks on the per-uurl semaphore.
    let (a2_tx, a2_rx) = tokio::sync::oneshot::channel();
    let a2_done = Arc::new(AtomicBool::new(false));
    {
        let (s, done) = (s.clone(), a2_done.clone());
        tokio::spawn(async move {
            let g = s.submit("A", "https://api/A", 0, false, false, "").await.unwrap();
            done.store(true, Ordering::SeqCst);
            let _ = a2_tx.send(g);
        });
    }
    settle().await;
    assert!(!a2_done.load(Ordering::SeqCst), "per-uurl limit 1 must block a second A");

    // uurl B is isolated: grants immediately despite A being saturated.
    let g_b = s.submit("B", "https://api/B", 0, false, false, "").await.unwrap();
    assert_eq!(s.stats().in_flight, 2);

    // Releasing A unblocks the parked A submit.
    drop(g_a);
    let mut g_a2 = a2_rx.await.unwrap();
    assert!(a2_done.load(Ordering::SeqCst));

    g_a2.release();
    g_a2.release(); // idempotent
    drop(g_b);
    assert_eq!(s.stats().in_flight, 0);
}

#[tokio::test(start_paused = true)]
async fn max_at_once_caps_global_in_flight() {
    let s = sched(
        r#"{"queue": {"max_at_once": 2, "max_pending": 100, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    let g1 = s.submit("A", "https://api/A", 0, false, false, "").await.unwrap();
    let g2 = s.submit("B", "https://api/B", 0, false, false, "").await.unwrap();

    // Third in-flight blocks on the implicit global rule regardless of uurl.
    let c_done = Arc::new(AtomicBool::new(false));
    let c_handle = {
        let (s, done) = (s.clone(), c_done.clone());
        tokio::spawn(async move {
            let g = s.submit("C", "https://api/C", 0, false, false, "").await.unwrap();
            done.store(true, Ordering::SeqCst);
            drop(g);
        })
    };
    settle().await;
    assert!(!c_done.load(Ordering::SeqCst), "max_at_once=2 must block a third in-flight");
    assert_eq!(s.stats().pending, 1);

    drop(g1);
    c_handle.await.unwrap();
    assert!(c_done.load(Ordering::SeqCst));
    drop(g2);
    assert_eq!(s.stats().in_flight, 0);
}

// --------------------------------------------------------------- 6. priority

#[tokio::test(start_paused = true)]
async fn backpressure_waiters_wake_in_priority_order() {
    // capacity-1 queue: one submit stuck in throttle occupies max_pending.
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": null, "max_pending": 1, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    let start = Instant::now();
    let grants: Arc<Mutex<Vec<(String, Duration)>>> = Arc::new(Mutex::new(Vec::new()));

    let submit = |name: &str, priority: i32| {
        let s = s.clone();
        let grants = grants.clone();
        let name = name.to_owned();
        tokio::spawn(async move {
            s.submit("u", "https://api/u", priority, false, false, "").await.unwrap();
            grants.lock().unwrap().push((name, Instant::now() - start));
        })
    };

    // Spends the t0 throttle token so everyone after must wait.
    let first = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    drop(first);

    let blocker = submit("blocker", 0); // occupies the single pending slot
    settle().await;
    let low = submit("low", 0); // parks at the door
    settle().await;
    let high = submit("high", 10); // parks at the door, higher priority
    settle().await;

    for h in [blocker, low, high] {
        h.await.unwrap();
    }

    let order = grants.lock().unwrap().clone();
    let names: Vec<&str> = order.iter().map(|(n, _)| n.as_str()).collect();
    assert_eq!(names, vec!["blocker", "high", "low"], "priority 10 must beat priority 0");
    assert_eq!(order[0].1, 1000 * MS);
    assert_eq!(order[1].1, 2000 * MS);
    assert_eq!(order[2].1, 3000 * MS);
}

// --------------------------------------------------------------- 7. pause + from_hook

#[tokio::test(start_paused = true)]
async fn pause_delays_grant_and_scopes_apply() {
    let s = sched("{}");
    s.pause("client", 5.0);
    s.pause("client", 2.0); // shorter pause never shrinks the existing one

    let stats = s.stats();
    let left = stats.paused.get("client").copied().unwrap_or(0.0);
    assert!((left - 5.0).abs() < 0.01, "paused stats must report ~5s, got {left}");

    let start = Instant::now();
    s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, 5000 * MS);

    // uurl-scoped pause gates only that lane.
    let s2 = sched("{}");
    s2.pause("U", 3.0);
    let start = Instant::now();
    s2.submit("V", "https://api/V", 0, false, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, Duration::ZERO);
    s2.submit("U", "https://api/U", 0, false, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, 3000 * MS);
}

#[tokio::test(start_paused = true)]
async fn from_hook_bypasses_pause_concurrency_and_throttle() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": 1, "max_pending": 100, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    // Exhaust the throttle window AND the only concurrency slot, then pause.
    let g1 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    s.pause("client", 100.0);
    s.pause("u", 100.0);

    let start = Instant::now();
    let hook_grant = s.submit("u", "https://api/u", 0, true, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, Duration::ZERO, "from_hook must bypass everything");

    drop(hook_grant);
    drop(g1);
    assert_eq!(s.stats().in_flight, 0);
}

#[tokio::test(start_paused = true)]
async fn from_hook_respects_throttle_when_throttle_in_hooks() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": null, "max_pending": 100, "on_full": "wait",
                      "throttle_in_hooks": true}}"#,
    );
    s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    let start = Instant::now();
    s.submit("u", "https://api/u", 0, true, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, 1000 * MS, "throttle_in_hooks=true must throttle hooks");
}

// --------------------------------------------------------------- 8. no_throttle

#[tokio::test(start_paused = true)]
async fn no_throttle_bypasses_only_throttle() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": 1, "max_pending": 100, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    // Spend the throttle token, free the concurrency slot.
    let g1 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    drop(g1);

    // Throttle bypassed: immediate grant despite the exhausted window.
    let start = Instant::now();
    let nt = s.submit("u", "https://api/u", 0, false, true, "").await.unwrap();
    assert_eq!(Instant::now() - start, Duration::ZERO);

    // Concurrency NOT bypassed: a second no_throttle submit blocks on max_at_once.
    let done = Arc::new(AtomicBool::new(false));
    let handle = {
        let (s, done) = (s.clone(), done.clone());
        tokio::spawn(async move {
            s.submit("u", "https://api/u", 0, false, true, "").await.unwrap();
            done.store(true, Ordering::SeqCst);
        })
    };
    settle().await;
    assert!(!done.load(Ordering::SeqCst), "no_throttle must still respect concurrency");
    drop(nt);
    handle.await.unwrap();
    assert!(done.load(Ordering::SeqCst));
}

// --------------------------------------------------------------- 9. on_full = raise

#[tokio::test(start_paused = true)]
async fn queue_full_raises_when_on_full_raise() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": null, "max_pending": 1, "on_full": "raise",
                      "throttle_in_hooks": false}}"#,
    );
    s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();

    // Occupies the single pending slot in its throttle sleep.
    let blocked = {
        let s = s.clone();
        tokio::spawn(async move {
            s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
        })
    };
    settle().await;

    let res = s.submit("u", "https://api/u", 0, false, false, "").await;
    assert_eq!(res.err(), Some(SubmitError::QueueFull));
    blocked.await.unwrap();
}

// --------------------------------------------------------------- 10. cancel safety

#[tokio::test(start_paused = true)]
async fn cancelled_submit_and_grant_drop_release_all_capacity() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]}}"#,
    );
    let start = Instant::now();
    let g1 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    assert_eq!(s.stats().in_flight, 1);
    assert_eq!(s.stats().pending, 0);

    // Park a second submit mid-throttle-sleep, then cancel it (asyncio-style).
    let victim = {
        let s = s.clone();
        tokio::spawn(async move {
            let _ = s.submit("u", "https://api/u", 0, false, false, "").await;
        })
    };
    settle().await;
    assert_eq!(s.stats().pending, 1, "sleeping submit must occupy pending");

    victim.abort();
    let join = victim.await;
    assert!(join.unwrap_err().is_cancelled());
    assert_eq!(s.stats().pending, 0, "cancelled submit must free pending capacity");
    assert_eq!(s.stats().in_flight, 1, "the held grant is untouched");

    drop(g1);
    assert_eq!(s.stats().in_flight, 0, "grant drop must release in_flight");

    // Later submits proceed; the cancelled one never reserved a window token,
    // so only g1's t0 stamp gates us: grant lands exactly at t=1000ms.
    s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    assert_eq!(Instant::now() - start, 1000 * MS);
}

#[tokio::test(start_paused = true)]
async fn cancelled_backpressure_waiter_passes_capacity_on() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]},
            "queue": {"max_at_once": null, "max_pending": 1, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();

    // blocker occupies the single pending slot until t=1000.
    let blocker = {
        let s = s.clone();
        tokio::spawn(async move {
            s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
        })
    };
    settle().await;

    // Two waiters park at the door; cancel the higher-priority one.
    let victim = {
        let s = s.clone();
        tokio::spawn(async move {
            let _ = s.submit("u", "https://api/u", 10, false, false, "").await;
        })
    };
    settle().await;
    let survivor_done = Arc::new(AtomicBool::new(false));
    let survivor = {
        let (s, done) = (s.clone(), survivor_done.clone());
        tokio::spawn(async move {
            s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
            done.store(true, Ordering::SeqCst);
        })
    };
    settle().await;
    assert_eq!(s.stats().pending, 3, "1 capacity holder + 2 parked waiters");

    victim.abort();
    let _ = victim.await;
    assert_eq!(s.stats().pending, 2, "abandoned waiter must leave the stats");

    blocker.await.unwrap();
    survivor.await.unwrap();
    assert!(survivor_done.load(Ordering::SeqCst), "capacity must skip the cancelled waiter");
}

// --------------------------------------------------------------- close

#[tokio::test(start_paused = true)]
async fn close_fails_new_and_parked_submits() {
    let s = sched(
        r#"{"queue": {"max_at_once": 1, "max_pending": 1, "on_full": "wait",
                      "throttle_in_hooks": false}}"#,
    );
    let g1 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();

    // Occupies pending, blocked on the concurrency slot.
    let blocked = {
        let s = s.clone();
        tokio::spawn(async move { s.submit("u", "https://api/u", 0, false, false, "").await })
    };
    settle().await;
    // Parked at the backpressure door.
    let parked = {
        let s = s.clone();
        tokio::spawn(async move { s.submit("u", "https://api/u", 0, false, false, "").await })
    };
    settle().await;

    s.close();
    assert_eq!(
        s.submit("u", "https://api/u", 0, false, false, "").await.err(),
        Some(SubmitError::Closed)
    );
    assert_eq!(parked.await.unwrap().err(), Some(SubmitError::Closed));

    drop(g1); // frees the slot: the mid-flight submit completes normally
    assert!(blocked.await.unwrap().is_ok());
}

// --------------------------------------------------------------- stats shape

#[tokio::test(start_paused = true)]
async fn stats_snapshot_serializes_to_expected_shape() {
    let s = sched(
        r#"{"throttle": {"mode": "exact", "rules":
                [{"id": 0, "match": ".*", "limit": 1, "per": 1.0}]}}"#,
    );
    let _g1 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap();
    let _g2 = s.submit("u", "https://api/u", 0, false, false, "").await.unwrap(); // throttled once
    s.pause("client", 9.0);

    let json = serde_json::to_value(s.stats()).expect("stats serialize");
    assert_eq!(json["in_flight"], 2);
    assert_eq!(json["pending"], 0);
    assert_eq!(json["throttle_hits"]["0"], 1);
    assert_eq!(json["throttled_by_uurl"]["u"], 1);
    assert!(json["paused"]["client"].as_f64().unwrap() > 8.9);
}

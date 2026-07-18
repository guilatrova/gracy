//! The queue IS the throttle — admission control for every request.
//!
//! Mirrors the observable semantics of `python/gracy/scheduler_py.PyScheduler`
//! (the executable spec). Per-submit order:
//!
//! ```text
//! backpressure admission (max_pending)
//!   -> acquire ALL matching concurrency semaphores, in rule-id order
//!      (queue.max_at_once is the implicit global rule id -1, acquired first)
//!   -> pause gate (checked BEFORE throttle so paused requests spend no tokens)
//!   -> throttle wait + atomic reserve
//!   -> Grant (in_flight += 1)
//! ```
//!
//! `from_hook=true` bypasses concurrency semaphores AND pause gates, and
//! bypasses throttle unless the plan sets `queue.throttle_in_hooks`.
//! `no_throttle=true` bypasses throttle only (replay-hit path).
//!
//! Cancellation safety: every stage frees itself on Drop — pending capacity
//! via [`PendingGuard`], concurrency slots via `OwnedSemaphorePermit` — so an
//! asyncio-cancelled submit (dropped future) rolls back cleanly. As in the
//! reference, max_pending capacity is released when the submit RESOLVES
//! (grant or failure), not when the Grant is released.

pub mod concurrency;
pub mod throttle;

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::fmt;
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::time::Duration;

use parking_lot::{Mutex, RwLock};
use serde::Serialize;
use tokio::sync::{Notify, OwnedSemaphorePermit};
use tokio::time::Instant;

use self::concurrency::{CompiledConcRule, ConcurrencyMap, MAX_AT_ONCE_RULE_ID};
use self::throttle::{CompiledThrottleRule, SlidingWindow};
use crate::plan::Plan;

/// Pause scope that gates every request regardless of uurl.
pub const CLIENT_SCOPE: &str = "client";

// --------------------------------------------------------------------------- errors

/// Why a `submit()` was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubmitError {
    /// Queue saturated and the plan says `on_full="raise"` (-> GracyQueueFull).
    QueueFull,
    /// Scheduler closed before or while waiting (-> GracyClientClosedError).
    Closed,
}

impl fmt::Display for SubmitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SubmitError::QueueFull => write!(f, "queue is full and on_full='raise'"),
            SubmitError::Closed => write!(f, "submit() on a closed scheduler"),
        }
    }
}

impl std::error::Error for SubmitError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OnFull {
    Wait,
    Raise,
}

// --------------------------------------------------------------------------- stats

/// Point-in-time observability snapshot (serde-serializable for the bindings).
/// Shape mirrors `PyScheduler.stats()`.
#[derive(Debug, Clone, Serialize)]
pub struct StatsSnapshot {
    /// Every submit not yet granted: capacity holders + parked waiters.
    pub pending: usize,
    /// Granted permits not yet released.
    pub in_flight: usize,
    /// rule id -> submits that waited on that rule (once per rule per submit).
    pub throttle_hits: HashMap<u32, u64>,
    /// uurl -> submits that throttled at least once (once per submit).
    pub throttled_by_uurl: HashMap<String, u64>,
    /// scope -> seconds of pause left (only active pauses).
    pub paused: HashMap<String, f64>,
}

// --------------------------------------------------------------------------- backpressure waiters

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum WaitState {
    /// Parked at the door.
    Waiting,
    /// Capacity was reserved on this waiter's behalf (pending already += 1).
    Reserved,
    /// Scheduler closed while parked; no capacity reserved.
    Closed,
    /// The submit future was dropped while parked; skip on pop.
    Abandoned,
}

#[derive(Debug)]
struct WaiterSlot {
    state: Mutex<WaitState>,
    notify: Notify,
}

struct WaiterEntry {
    priority: i32,
    seq: u64,
    slot: Arc<WaiterSlot>,
}

impl PartialEq for WaiterEntry {
    fn eq(&self, other: &Self) -> bool {
        self.priority == other.priority && self.seq == other.seq
    }
}
impl Eq for WaiterEntry {}
impl PartialOrd for WaiterEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for WaiterEntry {
    /// Max-heap: highest priority first, FIFO (lowest seq) within a priority.
    fn cmp(&self, other: &Self) -> Ordering {
        self.priority
            .cmp(&other.priority)
            .then_with(|| other.seq.cmp(&self.seq))
    }
}

// --------------------------------------------------------------------------- shared state

#[derive(Default)]
struct SchedState {
    /// Submits occupying max_pending capacity (admitted, not yet granted).
    pending: usize,
    waiters: BinaryHeap<WaiterEntry>,
    /// Abandoned entries still sitting in `waiters` (lazily skipped on pop).
    abandoned: usize,
    seq: u64,
    closed: bool,
}

struct Inner {
    throttle_meta: Vec<CompiledThrottleRule>,
    /// Index-aligned with `throttle_meta`; ONE mutex over all windows makes
    /// the multi-rule check + reserve atomic (mirrors asyncio's no-await
    /// atomicity in the reference).
    windows: Mutex<Vec<SlidingWindow>>,
    /// Sorted by rule id ascending — max_at_once (id -1) acquired first.
    conc_rules: Vec<CompiledConcRule>,
    semaphores: ConcurrencyMap,

    max_pending: usize,
    on_full: OnFull,
    throttle_in_hooks: bool,
    throttle_mode: String,

    state: Mutex<SchedState>,
    in_flight: AtomicUsize,
    /// scope ("client" | uurl) -> pause deadline.
    paused: RwLock<HashMap<String, Instant>>,
    throttle_hits: Mutex<HashMap<u32, u64>>,
    throttled_by_uurl: Mutex<HashMap<String, u64>>,
}

impl Inner {
    /// Hand freed capacity to the highest-priority (then oldest) parked
    /// submit. Caller holds the state lock.
    fn wake_next_locked(&self, st: &mut SchedState) {
        while st.pending < self.max_pending {
            let Some(entry) = st.waiters.pop() else { return };
            let mut ws = entry.slot.state.lock();
            match *ws {
                WaitState::Waiting => {
                    *ws = WaitState::Reserved;
                    st.pending += 1; // reserved on the waiter's behalf
                    drop(ws);
                    entry.slot.notify.notify_one();
                }
                WaitState::Abandoned => {
                    st.abandoned = st.abandoned.saturating_sub(1);
                }
                WaitState::Reserved | WaitState::Closed => {}
            }
        }
    }
}

/// Occupies one unit of max_pending capacity; frees it (and wakes the next
/// backpressure waiter) on drop — including when the submit future is
/// cancelled mid-way.
struct PendingGuard {
    inner: Arc<Inner>,
}

impl Drop for PendingGuard {
    fn drop(&mut self) {
        let mut st = self.inner.state.lock();
        st.pending = st.pending.saturating_sub(1);
        self.inner.wake_next_locked(&mut st);
    }
}

/// Cancel-safety for a submit parked at the backpressure door: if the future
/// is dropped while waiting, either pass reserved capacity on or mark the
/// heap entry abandoned. Disarmed once the wait resolves normally.
struct WaitGuard {
    inner: Arc<Inner>,
    slot: Arc<WaiterSlot>,
    armed: bool,
}

impl Drop for WaitGuard {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        let mut st = self.inner.state.lock();
        let mut ws = self.slot.state.lock();
        match *ws {
            WaitState::Reserved => {
                // Capacity was already handed to us — pass it on.
                drop(ws);
                st.pending = st.pending.saturating_sub(1);
                self.inner.wake_next_locked(&mut st);
            }
            WaitState::Waiting => {
                *ws = WaitState::Abandoned;
                drop(ws);
                st.abandoned += 1;
            }
            WaitState::Closed | WaitState::Abandoned => {}
        }
    }
}

// --------------------------------------------------------------------------- grant

struct GrantHeld {
    /// Concurrency slots, released (in reverse order, via Drop) with the grant.
    _permits: Vec<OwnedSemaphorePermit>,
    inner: Arc<Inner>,
}

impl Drop for GrantHeld {
    fn drop(&mut self) {
        self.inner.in_flight.fetch_sub(1, AtomicOrdering::Relaxed);
    }
}

/// Held admission: throttle tokens spent + concurrency slots acquired.
///
/// Dropping the grant (or calling [`Grant::release`], which is idempotent)
/// releases the concurrency slots and decrements `in_flight`.
#[derive(Debug)]
pub struct Grant {
    held: Option<GrantHeld>,
    granted_at: Instant,
}

impl fmt::Debug for GrantHeld {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GrantHeld")
            .field("permits", &self._permits.len())
            .finish()
    }
}

impl Grant {
    /// The instant admission was granted (throttle tokens spent).
    pub fn granted_at(&self) -> Instant {
        self.granted_at
    }

    /// Release concurrency slots + in_flight. Idempotent — the pipeline calls
    /// it in a finally block; a plain drop does the same thing once.
    pub fn release(&mut self) {
        self.held.take();
    }
}

// --------------------------------------------------------------------------- scheduler

/// The queue: owns throttling, concurrency, priorities, backpressure, and
/// pause gates. Cheap to clone (all state behind one `Arc`).
#[derive(Clone)]
pub struct Scheduler {
    inner: Arc<Inner>,
}

impl fmt::Debug for Scheduler {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Scheduler")
            .field("throttle_rules", &self.inner.throttle_meta.len())
            .field("conc_rules", &self.inner.conc_rules.len())
            .field("max_pending", &self.inner.max_pending)
            .finish()
    }
}

impl Scheduler {
    /// Compile a scheduler from the plan. Errors on invalid rule regexes
    /// (user input — never panics).
    pub fn new(plan: &Plan) -> Result<Self, String> {
        let mut throttle_meta = Vec::with_capacity(plan.throttle.rules.len());
        let mut windows = Vec::with_capacity(plan.throttle.rules.len());
        for rule in &plan.throttle.rules {
            let regex = regex::Regex::new(&rule.match_regex)
                .map_err(|e| format!("invalid throttle rule {} regex {:?}: {e}", rule.id, rule.match_regex))?;
            throttle_meta.push(CompiledThrottleRule { id: rule.id, regex });
            windows.push(SlidingWindow::new(rule.limit, Duration::from_secs_f64(rule.per.max(0.0))));
        }

        let mut conc_rules = Vec::with_capacity(plan.concurrency.len() + 1);
        for rule in &plan.concurrency {
            let regex = match &rule.match_regex {
                Some(pattern) => Some(
                    regex::Regex::new(pattern).map_err(|e| {
                        format!("invalid concurrency rule {} regex {pattern:?}: {e}", rule.id)
                    })?,
                ),
                None => None,
            };
            conc_rules.push(CompiledConcRule {
                id: i64::from(rule.id),
                regex,
                limit: rule.limit,
                per_uurl: rule.per_uurl,
            });
        }
        // queue.max_at_once is an implicit GLOBAL concurrency rule. id=-1
        // keeps it first in rule-id acquisition order, ahead of every user rule.
        if let Some(max_at_once) = plan.queue.max_at_once {
            conc_rules.push(CompiledConcRule {
                id: MAX_AT_ONCE_RULE_ID,
                regex: None,
                limit: max_at_once,
                per_uurl: false,
            });
        }
        conc_rules.sort_by_key(|r| r.id); // acquisition order: rule-id ascending

        let on_full = if plan.queue.on_full == "raise" {
            OnFull::Raise
        } else {
            OnFull::Wait // reference: anything but "raise" waits
        };

        Ok(Self {
            inner: Arc::new(Inner {
                throttle_meta,
                windows: Mutex::new(windows),
                conc_rules,
                semaphores: ConcurrencyMap::default(),
                max_pending: plan.queue.max_pending as usize,
                on_full,
                throttle_in_hooks: plan.queue.throttle_in_hooks,
                throttle_mode: plan.throttle.mode.clone(),
                state: Mutex::new(SchedState::default()),
                in_flight: AtomicUsize::new(0),
                paused: RwLock::new(HashMap::new()),
                throttle_hits: Mutex::new(HashMap::new()),
                throttled_by_uurl: Mutex::new(HashMap::new()),
            }),
        })
    }

    /// Parse `json` (the scheduler_plan emitted by `gracy.plan.compile_plan`)
    /// and compile a scheduler from it.
    pub fn from_plan_json(json: &str) -> Result<Self, String> {
        Self::new(&Plan::from_json(json)?)
    }

    /// `"exact"` or `"smooth"` (both use the exact sliding window for now —
    /// same external contract, stricter timing).
    pub fn throttle_mode(&self) -> &str {
        &self.inner.throttle_mode
    }

    // ------------------------------------------------------------ admission

    /// Admission control. Resolves when the request may go on the wire NOW.
    pub async fn submit(
        &self,
        uurl: &str,
        url: &str,
        priority: i32,
        from_hook: bool,
        no_throttle: bool,
        conc_extra: &str,
    ) -> Result<Grant, SubmitError> {
        if self.inner.state.lock().closed {
            return Err(SubmitError::Closed);
        }

        // Occupies pending capacity until this submit resolves (grant or
        // failure or cancellation) — mirrors the reference's `finally`.
        let _pending = self.admit(priority).await?;

        let mut permits: Vec<OwnedSemaphorePermit> = Vec::new();
        if !from_hook {
            for rule in &self.inner.conc_rules {
                // already sorted by rule id
                if rule.matches(uurl) {
                    let sem = self.inner.semaphores.semaphore_for(rule, uurl, conc_extra);
                    let permit = sem
                        .acquire_owned()
                        .await
                        .map_err(|_| SubmitError::Closed)?;
                    permits.push(permit);
                }
            }
            // Pause gate BEFORE throttle: paused requests spend no window
            // tokens (they hold their concurrency slots — gates are short).
            self.pause_gate(uurl).await;
        }
        if !(no_throttle || (from_hook && !self.inner.throttle_in_hooks)) {
            self.throttle(uurl, url).await;
        }

        self.inner.in_flight.fetch_add(1, AtomicOrdering::Relaxed);
        Ok(Grant {
            held: Some(GrantHeld { _permits: permits, inner: self.inner.clone() }),
            granted_at: Instant::now(),
        })
        // _pending drops here: pending -= 1, wake next backpressure waiter.
    }

    /// max_pending backpressure. When full: raise, or park in priority order.
    async fn admit(&self, priority: i32) -> Result<PendingGuard, SubmitError> {
        let slot = {
            let mut st = self.inner.state.lock();
            if st.closed {
                return Err(SubmitError::Closed);
            }
            if st.pending < self.inner.max_pending {
                st.pending += 1;
                return Ok(PendingGuard { inner: self.inner.clone() });
            }
            if self.inner.on_full == OnFull::Raise {
                return Err(SubmitError::QueueFull);
            }
            let slot = Arc::new(WaiterSlot {
                state: Mutex::new(WaitState::Waiting),
                notify: Notify::new(),
            });
            let seq = st.seq;
            st.seq += 1;
            st.waiters.push(WaiterEntry { priority, seq, slot: slot.clone() });
            slot
        };

        let mut guard = WaitGuard { inner: self.inner.clone(), slot: slot.clone(), armed: true };
        loop {
            // Create the notified future BEFORE checking state so a wake
            // between check and await is never lost (Notify stores the permit).
            let notified = slot.notify.notified();
            match *slot.state.lock() {
                WaitState::Reserved => {
                    guard.armed = false; // capacity is ours; PendingGuard takes over
                    return Ok(PendingGuard { inner: self.inner.clone() });
                }
                WaitState::Closed => {
                    guard.armed = false;
                    return Err(SubmitError::Closed);
                }
                WaitState::Waiting => {}
                WaitState::Abandoned => unreachable!("abandoned only set by our own drop"),
            }
            notified.await;
        }
    }

    // ------------------------------------------------------------ pause gates

    /// Dispatcher-level gate. scope: `"client"` or a uurl. Extends the
    /// current pause if longer (never shortens).
    pub fn pause(&self, scope: &str, seconds: f64) {
        let until = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
        let mut paused = self.inner.paused.write();
        let entry = paused.entry(scope.to_owned()).or_insert(until);
        if until > *entry {
            *entry = until;
        }
    }

    async fn pause_gate(&self, uurl: &str) {
        loop {
            let now = Instant::now();
            let until = {
                let paused = self.inner.paused.read();
                let client = paused.get(CLIENT_SCOPE).copied();
                let lane = paused.get(uurl).copied();
                match (client, lane) {
                    (Some(a), Some(b)) => Some(a.max(b)),
                    (Some(a), None) => Some(a),
                    (None, Some(b)) => Some(b),
                    (None, None) => None,
                }
            };
            match until {
                Some(until) if until > now => {
                    // re-check after sleeping: the pause may have been extended
                    tokio::time::sleep(until - now).await;
                }
                _ => return,
            }
        }
    }

    // ------------------------------------------------------------ throttle

    /// Sliding-window wait + reserve. The final check and the reservation
    /// happen under ONE lock over all windows — admission can never
    /// over-commit a window even across threads.
    async fn throttle(&self, uurl: &str, url: &str) {
        let matching: Vec<usize> = self
            .inner
            .throttle_meta
            .iter()
            .enumerate()
            .filter(|(_, meta)| meta.regex.is_match(url))
            .map(|(i, _)| i)
            .collect();
        if matching.is_empty() {
            return;
        }

        let mut hit_rules: HashSet<u32> = HashSet::new(); // once per rule per submit
        let mut counted_uurl = false;
        loop {
            let now = Instant::now();
            let mut newly_hit: Vec<u32> = Vec::new();
            let wait = {
                let mut windows = self.inner.windows.lock();
                let mut wait = Duration::ZERO;
                for &i in &matching {
                    let next = windows[i].next_allowed(now); // NEVER before now
                    let rule_wait = next.saturating_duration_since(now);
                    if rule_wait > wait {
                        wait = rule_wait;
                    }
                    let id = self.inner.throttle_meta[i].id;
                    if !rule_wait.is_zero() && hit_rules.insert(id) {
                        newly_hit.push(id);
                    }
                }
                if wait.is_zero() {
                    // reserve on ALL matching rules — same lock as the check
                    for &i in &matching {
                        windows[i].reserve(now);
                    }
                }
                wait
            };

            if !newly_hit.is_empty() {
                let mut hits = self.inner.throttle_hits.lock();
                for id in newly_hit {
                    *hits.entry(id).or_insert(0) += 1;
                }
            }
            if wait.is_zero() {
                return;
            }
            if !counted_uurl {
                counted_uurl = true;
                *self
                    .inner
                    .throttled_by_uurl
                    .lock()
                    .entry(uurl.to_owned())
                    .or_insert(0) += 1;
            }
            tokio::time::sleep(wait).await;
        }
    }

    // ------------------------------------------------------------ lifecycle

    /// Close the scheduler: subsequent `submit()`s (and submits parked at the
    /// backpressure door) fail with [`SubmitError::Closed`].
    pub fn close(&self) {
        let mut st = self.inner.state.lock();
        st.closed = true;
        // Wake everyone parked at the door WITHOUT reserving capacity.
        while let Some(entry) = st.waiters.pop() {
            let mut ws = entry.slot.state.lock();
            match *ws {
                WaitState::Waiting => {
                    *ws = WaitState::Closed;
                    drop(ws);
                    entry.slot.notify.notify_one();
                }
                WaitState::Abandoned => {
                    st.abandoned = st.abandoned.saturating_sub(1);
                }
                WaitState::Reserved | WaitState::Closed => {}
            }
        }
    }

    // ------------------------------------------------------------ observability

    pub fn stats(&self) -> StatsSnapshot {
        let now = Instant::now();
        let (pending, live_waiters) = {
            let st = self.inner.state.lock();
            (st.pending, st.waiters.len().saturating_sub(st.abandoned))
        };
        let paused = self
            .inner
            .paused
            .read()
            .iter()
            .filter(|(_, &until)| until > now)
            .map(|(scope, &until)| (scope.clone(), (until - now).as_secs_f64()))
            .collect();
        StatsSnapshot {
            // pending = every submit not yet granted: capacity holders + parked waiters
            pending: pending + live_waiters,
            in_flight: self.inner.in_flight.load(AtomicOrdering::Relaxed),
            throttle_hits: self.inner.throttle_hits.lock().clone(),
            throttled_by_uurl: self.inner.throttled_by_uurl.lock().clone(),
            paused,
        }
    }
}

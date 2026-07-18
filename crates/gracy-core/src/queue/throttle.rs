//! Exact sliding-window throttle primitives.
//!
//! Mirrors `scheduler_py._ThrottleRule`: max `limit` grants in any trailing
//! `per` window. `next_allowed` is never in the past (fixes v1's negative-wait
//! burst bug) and is exactly `front + per` when the window is full (fixes
//! v1's full-window over-wait bug).
//!
//! The `Scheduler` keeps ALL rule windows behind a single mutex
//! (`Mutex<Vec<SlidingWindow>>`) so the multi-rule check + reserve is atomic —
//! rule metadata (id + compiled regex) lives in [`CompiledThrottleRule`].

use std::collections::VecDeque;
use std::time::Duration;

use tokio::time::Instant;

/// One rule's window state: monotonic grant instants, oldest first.
#[derive(Debug)]
pub struct SlidingWindow {
    limit: usize,
    per: Duration,
    deque: VecDeque<Instant>,
}

impl SlidingWindow {
    pub fn new(limit: u32, per: Duration) -> Self {
        Self { limit: limit as usize, per, deque: VecDeque::new() }
    }

    /// Drop stamps that have fallen out of the trailing window.
    /// Python: `while timestamps[0] <= now - per: popleft()`.
    pub fn evict(&mut self, now: Instant) {
        while let Some(&front) = self.deque.front() {
            if front + self.per <= now {
                self.deque.pop_front();
            } else {
                break;
            }
        }
    }

    /// The earliest instant a new grant is allowed. Either `now` (room left
    /// after eviction) or the instant the oldest in-window stamp expires —
    /// clamped so it is NEVER before `now`.
    pub fn next_allowed(&mut self, now: Instant) -> Instant {
        self.evict(now);
        if self.deque.len() < self.limit {
            return now;
        }
        match self.deque.front() {
            Some(&front) => (front + self.per).max(now),
            // limit == 0 (pathological): never allowed; report one window out.
            None => now + self.per,
        }
    }

    /// Spend a window token at `now`. Caller must have just checked
    /// `next_allowed(now) <= now` under the same lock. Evicts first so the
    /// `deque.len() <= limit` memory bound holds structurally — not only by
    /// caller discipline (v1's unbounded-history regression guard).
    pub fn reserve(&mut self, now: Instant) {
        self.evict(now);
        self.deque.push_back(now);
    }
}

/// Compiled per-rule metadata; window state lives in the scheduler's shared
/// `Mutex<Vec<SlidingWindow>>` (index-aligned with these rules).
#[derive(Debug)]
pub struct CompiledThrottleRule {
    pub id: u32,
    /// Matched (search semantics) against the FORMATTED url.
    pub regex: regex::Regex,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_allows_up_to_limit_then_reports_oldest_expiry() {
        let per = Duration::from_millis(300);
        let mut w = SlidingWindow::new(3, per);
        let t0 = Instant::now();

        for i in 0..3 {
            let now = t0 + Duration::from_millis(i * 10);
            assert_eq!(w.next_allowed(now), now);
            w.reserve(now);
        }
        // Full: next allowed is oldest + per, not further.
        let now = t0 + Duration::from_millis(50);
        assert_eq!(w.next_allowed(now), t0 + per);
        // At exactly oldest + per the oldest stamp evicts and a grant fits.
        let at_expiry = t0 + per;
        assert_eq!(w.next_allowed(at_expiry), at_expiry);
    }

    #[test]
    fn next_allowed_never_before_now() {
        let per = Duration::from_millis(100);
        let mut w = SlidingWindow::new(1, per);
        let t0 = Instant::now();
        w.reserve(t0);
        // Way past expiry: front + per is in the past — must clamp to now.
        let late = t0 + Duration::from_secs(10);
        assert_eq!(w.next_allowed(late), late);
    }

    #[test]
    fn window_history_stays_bounded_after_many_grants() {
        // v1 regression: its ThrottleController appended every request's
        // timestamp forever — memory grew with total traffic. The window
        // must hold AT MOST `limit` stamps no matter how many pass through.
        let per = Duration::from_millis(50);
        let limit = 25usize;
        let mut w = SlidingWindow::new(limit as u32, per);
        let t0 = Instant::now();

        let mut now = t0;
        for _ in 0..10_000 {
            let allowed = w.next_allowed(now);
            now = allowed.max(now);
            w.reserve(now);
            now += Duration::from_micros(500);
            assert!(w.deque.len() <= limit, "window grew past its limit: {}", w.deque.len());
        }
        assert!(w.deque.len() <= limit);
    }
}

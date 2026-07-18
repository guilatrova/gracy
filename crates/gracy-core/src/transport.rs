//! HTTP transport: send one request, return a fully buffered response.
//! No policy here — retries, throttling, and error wrapping live upstream.
//!
//! Mirrors `python/gracy/transports.py` (`TransportConfig` field names) and
//! `python/gracy/_types.py` (`RequestSpec` / `Response` shapes). Errors are a
//! typed enum so the PyO3 bindings can map them onto Python exception types:
//! `Timeout -> TimeoutError`, `Connect -> ConnectionError`,
//! `InvalidUrl -> ValueError`, `Other -> RuntimeError`.

use std::collections::HashSet;
use std::fmt;
use std::time::{Duration, Instant};

use serde::de::{Deserializer, MapAccess, SeqAccess, Visitor};
use serde::Deserialize;

// --------------------------------------------------------------------------- config

/// Connection-level knobs shared by every transport implementation.
///
/// Field names (and defaults) match `gracy.transports.TransportConfig`:
/// `base_headers` (mapping), `proxy`, `verify_tls`, `follow_redirects`,
/// `http2`. `base_headers` accepts either a JSON object (`{"k": "v"}`, the
/// Python dataclass shape) or a list of `[key, value]` pairs.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TransportConfig {
    #[serde(default, deserialize_with = "deserialize_header_pairs")]
    pub base_headers: Vec<(String, String)>,
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default = "default_true")]
    pub verify_tls: bool,
    #[serde(default = "default_true")]
    pub follow_redirects: bool,
    #[serde(default)]
    pub http2: bool,
}

impl Default for TransportConfig {
    fn default() -> Self {
        Self {
            base_headers: Vec::new(),
            proxy: None,
            verify_tls: true,
            follow_redirects: true,
            http2: false,
        }
    }
}

impl TransportConfig {
    /// Parse a config from the JSON emitted by the Python side.
    pub fn from_json(json: &str) -> Result<Self, String> {
        serde_json::from_str(json).map_err(|e| format!("invalid TransportConfig JSON: {e}"))
    }
}

fn default_true() -> bool {
    true
}

/// Accept `{"k": "v", ...}` (Python `Mapping`) or `[["k", "v"], ...]`.
fn deserialize_header_pairs<'de, D>(deserializer: D) -> Result<Vec<(String, String)>, D::Error>
where
    D: Deserializer<'de>,
{
    struct PairsVisitor;

    impl<'de> Visitor<'de> for PairsVisitor {
        type Value = Vec<(String, String)>;

        fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.write_str("a map of header name -> value, or a list of [name, value] pairs")
        }

        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
            let mut out = Vec::with_capacity(map.size_hint().unwrap_or(0));
            while let Some((k, v)) = map.next_entry::<String, String>()? {
                out.push((k, v));
            }
            Ok(out)
        }

        fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
            let mut out = Vec::with_capacity(seq.size_hint().unwrap_or(0));
            while let Some(pair) = seq.next_element::<(String, String)>()? {
                out.push(pair);
            }
            Ok(out)
        }
    }

    deserializer.deserialize_any(PairsVisitor)
}

// --------------------------------------------------------------------------- request/response

/// Everything needed to send one HTTP request (mirrors `RequestSpec` minus
/// the replay-only `uurl` field, which never crosses into the transport).
#[derive(Debug, Clone)]
pub struct RequestData {
    pub method: String,
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Option<Vec<u8>>,
    /// Seconds. `None` = no timeout at all (explicit opt-out, matching the
    /// Python contract) — never a hidden client default.
    pub timeout: Option<f64>,
}

/// Fully buffered response (mirrors `gracy._types.Response`).
#[derive(Debug, Clone)]
pub struct ResponseData {
    pub status: u16,
    /// Lowercase keys, response order preserved.
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
    /// Final URL after redirects.
    pub url: String,
    /// Seconds, wall-clock around send + full body read.
    pub elapsed: f64,
    /// `"HTTP/1.1"`, `"HTTP/2"`, ...
    pub http_version: String,
}

// --------------------------------------------------------------------------- error

/// Transport failure, categorized for the Python exception mapping.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    /// Request or body read exceeded the per-request timeout. -> `TimeoutError`
    Timeout(String),
    /// TCP/TLS connection could not be established. -> `ConnectionError`
    Connect(String),
    /// The URL (or request construction) was invalid. -> `ValueError`
    InvalidUrl(String),
    /// Anything else (protocol errors, decode failures, ...). -> `RuntimeError`
    Other(String),
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TransportError::Timeout(msg) => write!(f, "request timed out: {msg}"),
            TransportError::Connect(msg) => write!(f, "connection failed: {msg}"),
            TransportError::InvalidUrl(msg) => write!(f, "invalid URL: {msg}"),
            TransportError::Other(msg) => write!(f, "transport error: {msg}"),
        }
    }
}

impl std::error::Error for TransportError {}

fn classify(err: reqwest::Error) -> TransportError {
    // Walk the source chain so wrapped timeouts/connect failures (e.g. inside
    // a redirect or body-read error) still classify correctly.
    let msg = full_message(&err);
    if err.is_timeout() {
        TransportError::Timeout(msg)
    } else if err.is_connect() {
        TransportError::Connect(msg)
    } else if err.is_builder() {
        // Request could not even be constructed (malformed URL/parts).
        TransportError::InvalidUrl(msg)
    } else {
        TransportError::Other(msg)
    }
}

fn full_message(err: &(dyn std::error::Error + 'static)) -> String {
    let mut msg = err.to_string();
    let mut source = err.source();
    while let Some(s) = source {
        msg.push_str(": ");
        msg.push_str(&s.to_string());
        source = s.source();
    }
    msg
}

// --------------------------------------------------------------------------- transport

/// reqwest-backed transport. One instance wraps one connection pool.
#[derive(Debug, Clone)]
pub struct Transport {
    client: reqwest::Client,
    base_headers: Vec<(String, String)>,
}

impl Transport {
    /// Build a reqwest client honoring `config`.
    ///
    /// Notes on the mapping:
    /// - `follow_redirects`: `Policy::limited(10)` when true, `Policy::none()`
    ///   when false.
    /// - `verify_tls = false`: `danger_accept_invalid_certs(true)`.
    /// - `proxy`: applied to all schemes when set.
    /// - `http2`: reqwest already negotiates HTTP/2 via ALPN by default, so
    ///   `true` is a no-op (documented, NOT `http2_prior_knowledge`) and
    ///   `false` pins the client to HTTP/1.1 only.
    pub fn new(config: TransportConfig) -> Result<Self, String> {
        let redirect_policy = if config.follow_redirects {
            reqwest::redirect::Policy::limited(10)
        } else {
            reqwest::redirect::Policy::none()
        };

        let mut builder = reqwest::Client::builder().redirect(redirect_policy);

        if !config.verify_tls {
            builder = builder.danger_accept_invalid_certs(true);
        }

        if let Some(proxy_url) = &config.proxy {
            let proxy = reqwest::Proxy::all(proxy_url)
                .map_err(|e| format!("invalid proxy URL {proxy_url:?}: {e}"))?;
            builder = builder.proxy(proxy);
        }

        if !config.http2 {
            builder = builder.http1_only();
        }

        let client = builder
            .build()
            .map_err(|e| format!("failed to build HTTP client: {e}"))?;

        Ok(Self {
            client,
            base_headers: config.base_headers,
        })
    }

    /// Send one request and buffer the full body.
    ///
    /// `base_headers` are applied UNDER the request headers: any header name
    /// present on the request replaces the base value entirely. `elapsed`
    /// measures send + complete body read.
    pub async fn send(&self, req: RequestData) -> Result<ResponseData, TransportError> {
        let method = reqwest::Method::from_bytes(req.method.as_bytes())
            .map_err(|e| TransportError::Other(format!("invalid HTTP method {:?}: {e}", req.method)))?;

        let url: reqwest::Url = req
            .url
            .parse()
            .map_err(|e| TransportError::InvalidUrl(format!("{:?}: {e}", req.url)))?;

        let mut builder = self.client.request(method, url);

        // base_headers first, then request headers — request wins per name.
        let request_names: HashSet<String> =
            req.headers.iter().map(|(k, _)| k.to_ascii_lowercase()).collect();
        for (k, v) in &self.base_headers {
            if !request_names.contains(&k.to_ascii_lowercase()) {
                builder = builder.header(k, v);
            }
        }
        for (k, v) in &req.headers {
            builder = builder.header(k, v);
        }

        if let Some(body) = req.body {
            builder = builder.body(body);
        }

        if let Some(seconds) = req.timeout {
            if !seconds.is_finite() || seconds < 0.0 {
                return Err(TransportError::Other(format!(
                    "timeout must be a non-negative finite number of seconds, got {seconds}"
                )));
            }
            // reqwest's per-request timeout covers connect through full body read.
            builder = builder.timeout(Duration::from_secs_f64(seconds));
        }

        let started = Instant::now();
        let response = builder.send().await.map_err(classify)?;

        let status = response.status().as_u16();
        let final_url = response.url().to_string();
        let http_version = version_str(response.version());
        let headers: Vec<(String, String)> = response
            .headers()
            .iter()
            .map(|(k, v)| {
                // HeaderName is already lowercase; values decoded lossily.
                (
                    k.as_str().to_string(),
                    String::from_utf8_lossy(v.as_bytes()).into_owned(),
                )
            })
            .collect();

        let body = response.bytes().await.map_err(classify)?.to_vec();
        let elapsed = started.elapsed().as_secs_f64();

        Ok(ResponseData {
            status,
            headers,
            body,
            url: final_url,
            elapsed,
            http_version,
        })
    }
}

fn version_str(version: reqwest::Version) -> String {
    match version {
        reqwest::Version::HTTP_09 => "HTTP/0.9".to_string(),
        reqwest::Version::HTTP_10 => "HTTP/1.0".to_string(),
        reqwest::Version::HTTP_11 => "HTTP/1.1".to_string(),
        reqwest::Version::HTTP_2 => "HTTP/2".to_string(),
        reqwest::Version::HTTP_3 => "HTTP/3".to_string(),
        other => format!("{other:?}"),
    }
}

// --------------------------------------------------------------------------- unit tests

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_from_json_full_shape() {
        let cfg = TransportConfig::from_json(
            r#"{
                "base_headers": {"User-Agent": "gracy/2.0", "X-Env": "test"},
                "proxy": "http://localhost:9999",
                "verify_tls": false,
                "follow_redirects": false,
                "http2": true
            }"#,
        )
        .expect("full config parses");
        assert_eq!(cfg.base_headers.len(), 2);
        assert_eq!(cfg.proxy.as_deref(), Some("http://localhost:9999"));
        assert!(!cfg.verify_tls);
        assert!(!cfg.follow_redirects);
        assert!(cfg.http2);
    }

    #[test]
    fn config_from_json_defaults_match_python() {
        let cfg = TransportConfig::from_json("{}").expect("empty config parses");
        assert!(cfg.base_headers.is_empty());
        assert!(cfg.proxy.is_none());
        assert!(cfg.verify_tls);
        assert!(cfg.follow_redirects);
        assert!(!cfg.http2);
    }

    #[test]
    fn config_base_headers_accepts_pair_list() {
        let cfg = TransportConfig::from_json(r#"{"base_headers": [["a", "1"], ["b", "2"]]}"#)
            .expect("pair-list headers parse");
        assert_eq!(
            cfg.base_headers,
            vec![("a".to_string(), "1".to_string()), ("b".to_string(), "2".to_string())]
        );
    }

    #[test]
    fn config_rejects_unknown_fields() {
        assert!(TransportConfig::from_json(r#"{"verify": true}"#).is_err());
    }

    #[test]
    fn transport_error_display() {
        assert_eq!(
            TransportError::Timeout("deadline".into()).to_string(),
            "request timed out: deadline"
        );
        assert_eq!(
            TransportError::InvalidUrl("nope".into()).to_string(),
            "invalid URL: nope"
        );
    }
}

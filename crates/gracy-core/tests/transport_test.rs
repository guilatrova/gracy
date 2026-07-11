//! Integration tests for gracy_core::transport against a minimal in-test
//! HTTP/1.1 server (tokio TcpListener + hand-rolled responses; no dev-deps).

use std::time::Duration;

use gracy_core::transport::{RequestData, Transport, TransportConfig, TransportError};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

/// A request as captured by the test server: (request head, body bytes).
type CapturedRequest = (String, Vec<u8>);

/// Spawn a one-shot HTTP/1.1 server. Reads one full request (headers +
/// Content-Length body), optionally sleeps, writes `response` verbatim,
/// and sends the captured request through the returned receiver.
async fn spawn_server(
    response: &'static str,
    delay: Option<Duration>,
) -> (String, tokio::sync::oneshot::Receiver<CapturedRequest>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind test server");
    let addr = listener.local_addr().expect("local addr");
    let (tx, rx) = tokio::sync::oneshot::channel();

    tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.expect("accept");
        let mut buf = Vec::new();
        let mut chunk = [0u8; 4096];

        // Read until end of headers, then until Content-Length is satisfied.
        let (head_end, content_length) = loop {
            let n = socket.read(&mut chunk).await.expect("read request");
            if n == 0 {
                break (buf.len(), 0);
            }
            buf.extend_from_slice(&chunk[..n]);
            if let Some(pos) = find_header_end(&buf) {
                let head = String::from_utf8_lossy(&buf[..pos]);
                let content_length = head
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().ok())?
                    })
                    .unwrap_or(0);
                break (pos + 4, content_length);
            }
        };
        while buf.len() < head_end + content_length {
            let n = socket.read(&mut chunk).await.expect("read body");
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
        }

        if let Some(delay) = delay {
            tokio::time::sleep(delay).await;
        }

        socket.write_all(response.as_bytes()).await.expect("write response");
        socket.flush().await.expect("flush");

        let head = String::from_utf8_lossy(&buf[..head_end.saturating_sub(4)]).into_owned();
        let body = buf[head_end.min(buf.len())..].to_vec();
        let _ = tx.send((head, body));
    });

    (format!("http://{addr}"), rx)
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

fn get(url: String) -> RequestData {
    RequestData {
        method: "GET".to_string(),
        url,
        headers: vec![],
        body: None,
        timeout: Some(5.0),
    }
}

fn transport() -> Transport {
    Transport::new(TransportConfig::from_json("{}").expect("default config")).expect("build transport")
}

#[tokio::test]
async fn get_roundtrip_status_body_headers_elapsed() {
    let (base, _rx) = spawn_server(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Custom: Yes\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{\"ok\":\"gracy\"}\n",
        None,
    )
    .await;

    let resp = transport().send(get(format!("{base}/pokemon"))).await.expect("GET succeeds");

    assert_eq!(resp.status, 200);
    assert_eq!(resp.body, b"{\"ok\":\"gracy\"}\n");
    assert_eq!(resp.url, format!("{base}/pokemon"));
    assert_eq!(resp.http_version, "HTTP/1.1");
    assert!(resp.elapsed > 0.0, "elapsed must be > 0, got {}", resp.elapsed);
    // Header keys must come back lowercase even though the server sent mixed case.
    let content_type = resp.headers.iter().find(|(k, _)| k == "content-type");
    assert_eq!(content_type, Some(&("content-type".to_string(), "application/json".to_string())));
    let custom = resp.headers.iter().find(|(k, _)| k == "x-custom");
    assert_eq!(custom, Some(&("x-custom".to_string(), "Yes".to_string())));
    assert!(
        resp.headers.iter().all(|(k, _)| *k == k.to_ascii_lowercase()),
        "all header keys must be lowercase: {:?}",
        resp.headers
    );
}

#[tokio::test]
async fn post_body_reaches_server() {
    let (base, rx) = spawn_server(
        "HTTP/1.1 201 Created\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        None,
    )
    .await;

    let payload = b"{\"name\":\"pikachu\"}".to_vec();
    let req = RequestData {
        method: "POST".to_string(),
        url: format!("{base}/create"),
        headers: vec![("content-type".to_string(), "application/json".to_string())],
        body: Some(payload.clone()),
        timeout: Some(5.0),
    };

    let resp = transport().send(req).await.expect("POST succeeds");
    assert_eq!(resp.status, 201);

    let (head, body) = rx.await.expect("server captured request");
    assert!(head.starts_with("POST /create HTTP/1.1"), "unexpected request head: {head}");
    assert_eq!(body, payload, "server must receive the exact POST body");
}

#[tokio::test]
async fn per_request_timeout_maps_to_timeout_error() {
    let (base, _rx) = spawn_server(
        "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        Some(Duration::from_millis(500)),
    )
    .await;

    let mut req = get(format!("{base}/slow"));
    req.timeout = Some(0.1);

    let err = transport().send(req).await.expect_err("must time out");
    assert!(
        matches!(err, TransportError::Timeout(_)),
        "expected Timeout, got: {err:?}"
    );
}

#[tokio::test]
async fn connect_error_to_closed_port() {
    // Bind and immediately drop to get a port that is very likely closed.
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("addr");
    drop(listener);

    let err = transport()
        .send(get(format!("http://{addr}/nope")))
        .await
        .expect_err("must fail to connect");
    assert!(
        matches!(err, TransportError::Connect(_)),
        "expected Connect, got: {err:?}"
    );
}

#[tokio::test]
async fn invalid_url_maps_to_invalid_url_error() {
    let err = transport()
        .send(get("not a url at all".to_string()))
        .await
        .expect_err("must reject invalid URL");
    assert!(
        matches!(err, TransportError::InvalidUrl(_)),
        "expected InvalidUrl, got: {err:?}"
    );
}

#[tokio::test]
async fn base_headers_applied_under_request_headers() {
    let (base, rx) = spawn_server(
        "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        None,
    )
    .await;

    let config = TransportConfig::from_json(
        r#"{"base_headers": {"X-Base-Only": "from-base", "X-Both": "base-loses"}}"#,
    )
    .expect("config parses");
    let transport = Transport::new(config).expect("build transport");

    let mut req = get(format!("{base}/headers"));
    req.headers = vec![
        ("X-Both".to_string(), "request-wins".to_string()),
        ("X-Req-Only".to_string(), "from-request".to_string()),
    ];

    transport.send(req).await.expect("GET succeeds");

    let (head, _body) = rx.await.expect("server captured request");
    let sent: Vec<(String, String)> = head
        .lines()
        .skip(1)
        .filter_map(|line| {
            let (k, v) = line.split_once(':')?;
            Some((k.trim().to_ascii_lowercase(), v.trim().to_string()))
        })
        .collect();

    let value_of = |name: &str| {
        sent.iter()
            .filter(|(k, _)| k == name)
            .map(|(_, v)| v.clone())
            .collect::<Vec<_>>()
    };

    assert_eq!(value_of("x-base-only"), vec!["from-base"], "base-only header must be sent");
    assert_eq!(value_of("x-req-only"), vec!["from-request"], "request-only header must be sent");
    assert_eq!(
        value_of("x-both"),
        vec!["request-wins"],
        "request header must fully replace the base header (no duplicates)"
    );
}

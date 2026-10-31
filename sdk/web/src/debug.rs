// SPDX-License-Identifier: Apache-2.0
use super::{Options, Request};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::thread::{self, JoinHandle};
use std::time::Duration;

pub fn script() -> &'static str {
    include_str!("../bridge.js")
}

type Handler = dyn Fn(Request) -> Result<Value, String> + Send + Sync;

/// Dropping the server removes only its own listing and stops accepting work. In-flight host work must be bounded.
pub struct Server {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    listing: PathBuf,
    secret: String,
}

impl Server {
    pub fn start(
        options: Options,
        handle: impl Fn(Request) -> Result<Value, String> + Send + Sync + 'static,
    ) -> io::Result<Self> {
        if options.bundle_id.is_empty()
            || options.bundle_id.len() > 155
            || !options
                .bundle_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
            || options.windows.is_empty()
            || options.windows.contains(&0)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "register a bundle ID and this process's native webview windows",
            ));
        }
        let mut bytes = [0u8; 32];
        std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
        let secret: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let listener = TcpListener::bind("127.0.0.1:0")?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let folder = options
            .home
            .join("Library/Application Support/SimMirror/apps");
        fs::create_dir_all(&folder)?;
        fs::set_permissions(&folder, fs::Permissions::from_mode(0o700))?;
        let listing = folder.join(format!("{}.json", options.bundle_id));
        let temporary = folder.join(format!(".{}.tmp", secret));
        let device = format!(
            "macapp-{}-{:x}",
            &options.bundle_id[..options.bundle_id.len().min(42)],
            Sha256::digest(options.bundle_id.as_bytes())
        );
        let device = &device[..device.len() - 56];
        let body = json!({"protocol":1,"sdk_version":"0.1.0","device_udid":device,"bundle_id":options.bundle_id,
            "name":options.name,"pid":std::process::id(),"port":port,"secret":secret,"active":false,"platform":"macos", "started_at":time::OffsetDateTime::now_utc().format(&time::format_description::well_known::Rfc3339).map_err(io::Error::other)?});
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temporary)?;
        file.write_all(body.to_string().as_bytes())?;
        file.sync_all()?;
        fs::rename(&temporary, &listing)?;
        let stop = Arc::new(AtomicBool::new(false));
        let stopped = stop.clone();
        let token = secret.clone();
        let handler: Arc<Handler> = Arc::new(handle);
        let worker = thread::spawn(move || {
            while !stopped.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = serve(&mut stream, port, &token, &options.windows, &*handler);
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10))
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(Self {
            stop,
            worker: Some(worker),
            listing,
            secret,
        })
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        // A host callback is bounded by the host integration. Never block application shutdown on a UI-thread callback.
        self.worker.take();
        if fs::read(&self.listing)
            .ok()
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .and_then(|v| v["secret"].as_str().map(str::to_owned))
            .as_deref()
            == Some(&self.secret)
        {
            let _ = fs::remove_file(&self.listing);
        }
    }
}

fn equal(a: &str, b: &str) -> bool {
    a.len() == b.len()
        && a.bytes()
            .zip(b.bytes())
            .fold(0u8, |acc, (x, y)| acc | (x ^ y))
            == 0
}

fn parse(
    head: &str,
    body: &[u8],
    port: u16,
    secret: &str,
    windows: &[u32],
) -> Result<Request, (u16, &'static str)> {
    let mut lines = head.split("\r\n");
    let parts: Vec<_> = lines.next().unwrap_or("").split(' ').collect();
    if parts.len() != 3 || parts[2] != "HTTP/1.1" {
        return Err((400, "malformed HTTP request"));
    }
    let mut headers = std::collections::BTreeMap::new();
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line.split_once(':').ok_or((400, "malformed header"))?;
        if headers
            .insert(name.to_ascii_lowercase(), value.trim())
            .is_some()
        {
            return Err((400, "duplicate header"));
        }
    }
    if !equal(
        headers.get("authorization").copied().unwrap_or(""),
        &format!("Bearer {secret}"),
    ) {
        return Err((401, "authorization required"));
    }
    if headers.contains_key("origin")
        || headers.contains_key("transfer-encoding")
        || headers.get("host").copied() != Some(format!("127.0.0.1:{port}").as_str())
    {
        return Err((400, "only loopback protocol requests are accepted"));
    }
    let requested = if parts[0] == "POST" && parts[1] == "/v1/input" {
        let count = headers
            .get("content-length")
            .and_then(|s| s.parse::<usize>().ok())
            .ok_or((400, "input needs Content-Length"))?;
        if count != body.len() || !(1..=32768).contains(&count) {
            return Err((400, "input size is invalid"));
        }
        Request::Input(serde_json::from_slice(body).map_err(|_| (400, "malformed input"))?)
    } else if parts[0] == "GET"
        && parts[1].split('?').next() == Some("/v1/hierarchy")
        && body.is_empty()
    {
        let query: std::collections::BTreeMap<_, _> = parts[1]
            .split_once('?')
            .map(|(_, q)| q.split('&').filter_map(|p| p.split_once('=')).collect())
            .unwrap_or_default();
        let id = query
            .get("window_id")
            .and_then(|n| n.parse().ok())
            .ok_or((400, "choose a registered window"))?;
        let max = query
            .get("max_nodes")
            .unwrap_or(&"3000")
            .parse::<usize>()
            .map_err(|_| (400, "invalid max_nodes"))?;
        if max == 0 {
            return Err((400, "invalid max_nodes"));
        }
        Request::Hierarchy {
            window_id: id,
            max_nodes: max.min(3000),
        }
    } else {
        return Err((404, "unsupported endpoint"));
    };
    let id = match &requested {
        Request::Hierarchy { window_id, .. } => *window_id,
        Request::Input(v) => v["windowID"]
            .as_u64()
            .and_then(|n| u32::try_from(n).ok())
            .ok_or((400, "choose a registered window"))?,
    };
    if !windows.contains(&id) {
        return Err((403, "window is not registered by this app"));
    }
    Ok(requested)
}

fn serve(
    stream: &mut TcpStream,
    port: u16,
    secret: &str,
    windows: &[u32],
    handle: &Handler,
) -> io::Result<()> {
    // Darwin accepts inherit the listener's nonblocking flag; requests may arrive in several TCP packets.
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let mut bytes = Vec::new();
    let mut chunk = [0u8; 4096];
    let split = loop {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(());
        }
        bytes.extend_from_slice(&chunk[..n]);
        if let Some(at) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
            if at > 8192 {
                return Ok(());
            }
            break at;
        }
        if bytes.len() > 8192 {
            return Ok(());
        }
    };
    let head =
        String::from_utf8(bytes[..split].to_vec()).map_err(|_| io::ErrorKind::InvalidData)?;
    let count = head
        .split("\r\n")
        .filter_map(|line| line.split_once(':'))
        .find(|(k, _)| k.eq_ignore_ascii_case("content-length"))
        .and_then(|(_, v)| v.trim().parse::<usize>().ok())
        .unwrap_or(0);
    if count > 32768 {
        return Ok(());
    }
    while bytes.len() < split + 4 + count {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(());
        }
        bytes.extend_from_slice(&chunk[..n]);
    }
    let (status, body) = match parse(&head, &bytes[split + 4..], port, secret, windows) {
        Ok(request) => match handle(request) {
            Ok(value) => (200, value),
            Err(reason) => (
                422,
                json!({"error":{"code":"unsupported","message":reason.replace(secret,"[redacted]").chars().take(500).collect::<String>()}}),
            ),
        },
        Err((code, message)) => (code, json!({"error":{"code":"refused","message":message}})),
    };
    let encoded = body.to_string();
    if encoded.len() > 2 * 1024 * 1024 {
        return Ok(());
    }
    write!(stream,"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{encoded}",encoded.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn head(method: &str, path: &str, extra: &str) -> String {
        format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:5000\r\nAuthorization: Bearer private-test-secret\r\n{extra}")
    }
    #[test]
    fn requests_are_authenticated_bounded_and_confined_before_the_host_sees_them() {
        let get = head("GET", "/v1/hierarchy?window_id=7&max_nodes=9000", "");
        assert_eq!(
            parse(&get, b"", 5000, "private-test-secret", &[7]),
            Ok(Request::Hierarchy {
                window_id: 7,
                max_nodes: 3000
            })
        );
        for invalid in [
            get.replace("private-test-secret", "wrong"),
            get.replace("window_id=7", "window_id=8"),
            get.replace("max_nodes=9000", "max_nodes=0"),
            get.replace("127.0.0.1", "example.invalid"),
            format!("{get}Origin: https://page.invalid\r\n"),
            format!("{get}Authorization: duplicate\r\n"),
            format!("{get}Transfer-Encoding: chunked\r\n"),
            "broken".into(),
        ] {
            assert!(parse(&invalid, b"", 5000, "private-test-secret", &[7]).is_err());
        }
        let body = br#"{"kind":"tap","windowID":7,"x":1,"y":2}"#;
        let post = head(
            "POST",
            "/v1/input",
            &format!("Content-Length: {}\r\n", body.len()),
        );
        assert!(matches!(
            parse(&post, body, 5000, "private-test-secret", &[7]),
            Ok(Request::Input(_))
        ));
        assert!(parse(&post, b"{}", 5000, "private-test-secret", &[7]).is_err());
        assert!(parse(&post, body, 5000, "private-test-secret", &[8]).is_err());
        assert!(!equal("abc", "abd"));
        assert!(!equal("abc", "ab"));
    }
    #[test]
    fn fragmented_requests_wait_for_headers_and_body() {
        let folder =
            std::env::temp_dir().join(format!("sim-mirror-fragmented-{}", std::process::id()));
        let server = Server::start(
            Options {
                bundle_id: "dev.simmirror.Fragmented".into(),
                name: "Sample".into(),
                home: folder.clone(),
                windows: vec![7],
            },
            |_| Ok(json!({"accepted":true})),
        )
        .unwrap();
        let listing: Value = serde_json::from_slice(&fs::read(&server.listing).unwrap()).unwrap();
        let port = listing["port"].as_u64().unwrap();
        let mut stream = TcpStream::connect(format!("127.0.0.1:{port}")).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        thread::sleep(Duration::from_millis(30));
        let body = r#"{"kind":"tap","windowID":7,"x":1,"y":2}"#;
        write!(stream,"POST /v1/input HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {}\r\nContent-Length: {}\r\n",server.secret,body.len()).unwrap();
        thread::sleep(Duration::from_millis(30));
        stream.write_all(b"\r\n").unwrap();
        thread::sleep(Duration::from_millis(30));
        stream.write_all(body.as_bytes()).unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(response.starts_with("HTTP/1.1 200"));
        assert!(response.contains("accepted"));
        drop(server);
        fs::remove_dir_all(folder).unwrap();
    }
    #[test]
    fn a_private_listing_is_bound_to_this_process_and_newer_launches_are_not_removed() {
        let folder = std::env::temp_dir().join(format!(
            "sim-mirror-web-test-{}-{}",
            std::process::id(),
            time::OffsetDateTime::now_utc().unix_timestamp_nanos()
        ));
        let options = || Options {
            bundle_id: "dev.simmirror.Sample".into(),
            name: "Sample".into(),
            home: folder.clone(),
            windows: vec![7],
        };
        let one = Server::start(options(), |_| Ok(json!({}))).unwrap();
        let path = one.listing.clone();
        let first: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(first["pid"], std::process::id());
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(first["started_at"].as_str().unwrap().contains('T'));
        let two = Server::start(options(), |_| Ok(json!({}))).unwrap();
        assert_ne!(one.secret, two.secret);
        drop(one);
        assert!(path.exists());
        drop(two);
        assert!(!path.exists());
        fs::remove_dir_all(folder).unwrap();
    }
}

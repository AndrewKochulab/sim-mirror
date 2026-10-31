// SPDX-License-Identifier: Apache-2.0
//! Host-side Debug adapter for a registered webview. The host executes requests on its own UI thread.
//! No server, listing, injected script or input implementation is compiled into optimized Release builds.
use serde_json::Value;
use std::path::PathBuf;

pub struct Options {
    pub bundle_id: String,
    pub name: String,
    /// The application's home (its container home when sandboxed).
    pub home: PathBuf,
    /// Native window IDs belonging to webviews this host explicitly registered.
    pub windows: Vec<u32>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Request {
    Hierarchy { window_id: u32, max_nodes: usize },
    Input(Value),
}

#[cfg(debug_assertions)]
mod debug;
#[cfg(debug_assertions)]
pub use debug::{script, Server};

#[cfg(not(debug_assertions))]
pub struct Server;
#[cfg(not(debug_assertions))]
impl Server {
    pub fn start(
        _options: Options,
        _handle: impl Fn(Request) -> Result<Value, String> + Send + Sync + 'static,
    ) -> std::io::Result<Self> {
        Ok(Self)
    }
}
#[cfg(not(debug_assertions))]
pub fn script() -> &'static str {
    ""
}

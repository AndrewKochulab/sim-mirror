// SPDX-License-Identifier: Apache-2.0
// This entire module is excluded from Release, including injection and the host callback.
use serde_json::{json, Value};
use sim_mirror_web::{Options, Request, Server};
use std::{io, sync::Mutex, time::Duration};
use tauri::WebviewWindow;

pub fn start(window: WebviewWindow) -> Result<Mutex<Server>, Box<dyn std::error::Error>> {
    let native: &objc2_app_kit::NSWindow = unsafe { &*window.ns_window()?.cast() };
    let id = u32::try_from(native.windowNumber())?;
    let frame = native.frame();
    let content = native.contentLayoutRect();
    let content_top = frame.size.height - content.origin.y - content.size.height;
    let options = Options {
        bundle_id: "dev.simmirror.WebSample".into(),
        name: "Mac web sample".into(),
        home: std::env::var_os("HOME")
            .ok_or_else(|| io::Error::other("home is missing"))?
            .into(),
        windows: vec![id],
    };
    Ok(Mutex::new(Server::start(options, move |request| {
        let scale = window.scale_factor().map_err(|_| "window ended")?;
        let outer = window.outer_size().map_err(|_| "window ended")?;
        let offset = json!({"x":0,"y":content_top});
        let expression = match request {
            Request::Hierarchy {
                window_id,
                max_nodes,
            } => {
                if window_id != id {
                    return Err("wrong window".into());
                }
                let options = json!({"maxNodes":max_nodes,"offset":offset,
                    "app":{"bundle_id":"dev.simmirror.WebSample","name":"Mac web sample","pid":std::process::id(),"active":false},
                    "screen":{"width_pt":outer.width as f64/scale,"height_pt":outer.height as f64/scale,"scale":scale,"orientation":"portrait"}});
                format!("globalThis.__SimMirrorWeb.capture({options})")
            }
            Request::Input(command) => {
                format!("globalThis.__SimMirrorWeb.input({command},{offset})")
            }
        };
        // JSON serialization is the only interpolation of caller data. No secret is injected into the page.
        let js=format!("(()=>{{try{{{};return {{ok:true,value:{expression}}};}}catch(e){{return {{ok:false,reason:String(e.message).slice(0,500)}};}}}})()",sim_mirror_web::script());
        let (send, receive) = std::sync::mpsc::sync_channel(1);
        window
            .eval_with_callback(js, move |answer| {
                let _ = send.send(answer);
            })
            .map_err(|_| "webview ended")?;
        let raw = receive
            .recv_timeout(Duration::from_secs(2))
            .map_err(|_| "webview did not answer; inspect before retrying")?;
        let result: Value = serde_json::from_str(&raw).map_err(|_| "invalid webview result")?;
        if result["ok"] != true {
            return Err(result["reason"]
                .as_str()
                .unwrap_or("webview refused input")
                .into());
        }
        Ok(result["value"].clone())
    })?))
}

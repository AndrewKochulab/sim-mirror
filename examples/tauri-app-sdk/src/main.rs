// SPDX-License-Identifier: Apache-2.0
use tauri::{WebviewUrl, WebviewWindowBuilder};
#[cfg(debug_assertions)]
use tauri::Manager;
#[cfg(debug_assertions)]
mod mirror;
fn main() {
    tauri::Builder::default()
        .setup(|app| {
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            let window =
                WebviewWindowBuilder::new(app, "sample", WebviewUrl::App("index.html".into()))
                    .title("Mac web sample")
                    .inner_size(480.0, 640.0)
                    .focused(false)
                    .on_navigation(|url| {
                        url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
                    })
                    .build()?;
            #[cfg(debug_assertions)]
            if std::env::var_os("SIM_MIRROR_WEB_BRIDGE").is_some() {
                app.manage(mirror::start(window)?);
            }
            #[cfg(not(debug_assertions))]
            let _ = window;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("the sample app could not start");
}

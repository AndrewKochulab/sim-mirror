// SPDX-License-Identifier: Apache-2.0
fn main() {
    println!("{}", sim_mirror_web::script());
    let _server = sim_mirror_web::Server::start(
        sim_mirror_web::Options {
            bundle_id: "dev.simmirror.Probe".into(),
            name: "Probe".into(),
            home: std::env::temp_dir(),
            windows: vec![1],
        },
        |_| Err("no registered webview".into()),
    );
}

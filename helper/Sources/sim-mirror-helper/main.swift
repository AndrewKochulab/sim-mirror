// SPDX-License-Identifier: Apache-2.0
//
// SimMirror's native helper. SimMirror starts one per booted device and talks to it on a unix socket; see
// connectors/native in SimMirror for the other side.
import Darwin
import Foundation
import HelperCore
import HelperPlatform

setvbuf(stdout, nil, _IOLBF, 0)

func finish(_ code: Int32, _ message: String? = nil) -> Never {
    if let message { FileHandle.standardError.write(Data((message + "\n").utf8)) }
    exit(code)
}

let command: Command
do {
    command = try Command.parse(Array(CommandLine.arguments.dropFirst()))
} catch {
    finish(2, HelperFailure.from(error).message)
}

switch command {
case .version:
    let coreSimulator = try? SimulatorFrameworks.load(developerDir: SimulatorFrameworks.developerDir())
    print(String(decoding: JSON.encode(VersionReport(coreSimulator: coreSimulator ?? nil)), as: UTF8.self))
    exit(0)

case .selfCheck(let options):
    Task {
        do {
            let device = try SimulatorDevice(options: options)
            let report = await SelfCheckReport.run(device)
            print(String(decoding: JSON.encode(report), as: UTF8.self))
            finish(report.ok ? 0 : 1)
        } catch {
            let report = SelfCheckReport(parts: [.init(name: "device", ok: false, detail: HelperFailure.from(error).message)])
            print(String(decoding: JSON.encode(report), as: UTF8.self))
            finish(1)
        }
    }
    dispatchMain()

case .serve(let options):
    let log = Log(level: options.logLevel)
    signal(SIGPIPE, SIG_IGN)
    let device: SimulatorDevice
    let listener: UnixSocketListener
    do {
        device = try SimulatorDevice(options: options.device, idleKeyFrames: options.idleKeyFrames, log: log)
        listener = try UnixSocketListener(path: options.socket)
    } catch {
        finish(3, HelperFailure.from(error).message)
    }
    let router = Router(device: device)
    listener.accept { channel in
        Task { await Connection(channel: channel, router: router, log: log).run() }
    }
    log.info("serving \(options.device.udid) on \(options.socket) with CoreSimulator \(device.coreSimulator ?? "unknown")")

    // Let go when SimMirror asks, when the process that started this one is gone, or when the device shuts down.
    let stop: @Sendable (String) -> Void = { reason in
        log.info("stopping: \(reason)")
        listener.close()
        device.close()
        exit(0)
    }
    for code in [SIGTERM, SIGINT] {
        signal(code, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: code, queue: .main)
        source.setEventHandler { stop("signal \(code)") }
        source.resume()
        _ = Unmanaged.passRetained(source)
    }
    let watch = DispatchSource.makeTimerSource(queue: .main)
    watch.schedule(deadline: .now() + 1, repeating: 1)
    watch.setEventHandler {
        if let parent = options.parentPid, kill(parent, 0) != 0, errno == ESRCH { stop("SimMirror (pid \(parent)) is gone") }
        if !device.booted { stop("the simulator is no longer booted") }
    }
    watch.resume()
    dispatchMain()
}

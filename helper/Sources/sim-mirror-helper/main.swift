// SPDX-License-Identifier: Apache-2.0
//
// SimMirror's native helper. SimMirror starts one per booted simulator, or per cabled real device it shows, and talks
// to it on a unix socket; see connectors/native in SimMirror for the other side.
import Darwin
import Foundation
import HelperCore
import HelperPlatform

setvbuf(stdout, nil, _IOLBF, 0)

func finish(_ code: Int32, _ message: String? = nil) -> Never {
    if let message { FileHandle.standardError.write(Data((message + "\n").utf8)) }
    exit(code)
}

/// Serve `device` on a unix socket until SimMirror asks the helper to stop, the process that started it is gone, or
/// `gone` says why the device is.
func serve(
    _ device: Device, socket: String, parentPid: Int32?, log: Log, startWarming: () -> Void, gone: @escaping () -> String?,
    close: @escaping () -> Void
) -> Never {
    signal(SIGPIPE, SIG_IGN)
    let listener: UnixSocketListener
    do {
        listener = try UnixSocketListener(path: socket)
    } catch {
        finish(3, HelperFailure.from(error).message)
    }
    let router = Router(device: device)
    listener.accept { channel in
        Task { await Connection(channel: channel, router: router, log: log).run() }
    }
    startWarming()
    let stop: @Sendable (String) -> Void = { reason in
        log.info("stopping: \(reason)")
        listener.close()
        close()
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
        if let parentPid, kill(parentPid, 0) != 0, errno == ESRCH { stop("SimMirror (pid \(parentPid)) is gone") }
        if let reason = gone() { stop(reason) }
    }
    watch.resume()
    dispatchMain()
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

case .render(let path):
    Task {
        do {
            let job = try RenderJob.read(try Data(contentsOf: URL(fileURLWithPath: path)))
            let report = try await RecordingRenderer.run(job)
            print(String(decoding: JSON.encode(report), as: UTF8.self))
            finish(0)
        } catch {
            // On stdout, where SimMirror reads the answer, so it can say why.
            print(String(decoding: JSON.encode(["error": HelperFailure.from(error).message]), as: UTF8.self))
            finish(1)
        }
    }
    dispatchMain()

case .serve(let options):
    let log = Log(level: options.logLevel)
    let device: SimulatorDevice
    do {
        device = try SimulatorDevice(options: options.device, idleKeyFrames: options.idleKeyFrames, log: log)
    } catch {
        finish(3, HelperFailure.from(error).message)
    }
    log.info("serving \(options.device.udid) on \(options.socket) with CoreSimulator \(device.coreSimulator ?? "unknown")")
    serve(
        device, socket: options.socket, parentPid: options.parentPid, log: log, startWarming: device.startWarming,
        gone: { device.booted ? nil : "the simulator is no longer booted" }, close: device.close
    )

case .capture(let options):
    // macOS asks the Camera permission of the process responsible for this one; started again disclaimed, that is
    // this program itself, not the terminal or agent SimMirror runs under.
    if Disclaim.needed(ProcessInfo.processInfo.environment), let status = Disclaimed.run() {
        exit(status)
    }
    let log = Log(level: options.logLevel)
    let device = CaptureDevice(options: options, log: log)
    log.info("serving the screen of \(options.udid) on \(options.socket)")
    serve(
        device, socket: options.socket, parentPid: options.parentPid, log: log, startWarming: device.startWarming,
        gone: { device.connected ? nil : "the device's cable was unplugged" }, close: device.close
    )
}

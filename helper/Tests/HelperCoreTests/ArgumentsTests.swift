// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import HelperCore

@Suite struct ArgumentsTests {
    @Test func versionServeAndSelfCheckAreRead() throws {
        #expect(try Command.parse(["version"]) == .version)
        #expect(try Command.parse(["serve", "--udid", "U", "--socket", "/s"]) == .serve(ServeOptions(device: DeviceOptions(udid: "U"), socket: "/s")))
        #expect(
            try Command.parse(["serve", "--udid", "U", "--socket", "/s", "--parent-pid", "42", "--hid", "indigo", "--idle-key-frames", "off", "--log-level", "debug"])
                == .serve(ServeOptions(device: DeviceOptions(udid: "U", hid: .indigo), socket: "/s", parentPid: 42, idleKeyFrames: false, logLevel: .debug))
        )
        #expect(try Command.parse(["serve", "--udid", "U", "--socket", "/s", "--idle-key-frames", "on"]) == .serve(ServeOptions(device: DeviceOptions(udid: "U"), socket: "/s")))
        #expect(try Command.parse(["self-check", "--udid", "U", "--hid", "dtuhid"]) == .selfCheck(DeviceOptions(udid: "U", hid: .dtuhid)))
    }

    @Test(arguments: [
        ([], "usage:"),
        (["jump"], "not a command: jump"),
        (["version", "--udid", "U"], "not a flag here: --udid"),
        (["serve", "--socket", "/s"], "a device is named with --udid"),
        (["serve", "--udid", "U"], "serve needs --socket"),
        (["serve", "--udid", "U", "--socket", ""], "serve needs --socket"),
        (["serve", "--udid", "U", "--socket", "/s", "--parent-pid", "1"], "not a pid: 1"),
        (["serve", "--udid", "U", "--socket", "/s", "--idle-key-frames", "maybe"], "is on or off, not maybe"),
        (["serve", "--udid", "U", "--socket", "/s", "--log-level", "loud"], "not a log level: loud"),
        (["self-check", "--udid", "U", "--hid", "usb"], "--hid is auto, dtuhid or indigo, not usb"),
        (["self-check", "udid"], "not a flag: udid"),
        (["self-check", "--udid"], "--udid needs a value"),
    ] as [([String], String)])
    func whatCannotBeReadSaysWhy(arguments: [String], says: String) {
        do {
            _ = try Command.parse(arguments)
            Issue.record("\(arguments) was read")
        } catch {
            #expect((error as? HelperFailure)?.message.contains(says) == true)
            #expect((error as? HelperFailure)?.status == 400)
        }
    }

    @Test func theVersionReportIsJSON() {
        #expect(String(decoding: JSON.encode(VersionReport(coreSimulator: nil)), as: UTF8.self) == #"{"core_simulator":null,"version":"\#(HelperVersion.current)","wire":1}"#)
    }

    @Test func aSelfCheckChecksEveryPartAndSaysWhichFailed() async {
        let device = FakeDevice()
        let report = await SelfCheckReport.run(device)
        #expect(report.ok && report.parts.map(\.name) == ["screen", "screenshot", "input", "element tree"])
        #expect(report.parts.map(\.detail) == ["1206x2622 pixels, 402x874 points", "90x196, 2 bytes", "through dtuhid", "2 elements"])
        device.inputFailure = HelperFailure("no digitizer")
        let failing = await SelfCheckReport.run(device)
        #expect(!failing.ok && failing.parts[2] == .init(name: "input", ok: false, detail: "no digitizer"))
        #expect(String(decoding: JSON.encode(failing), as: UTF8.self).hasPrefix(#"{"ok":false,"parts":[{"detail":"1206x2622"#))
    }
}

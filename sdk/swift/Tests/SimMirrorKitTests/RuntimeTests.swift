// SPDX-License-Identifier: Apache-2.0
import Darwin
import Foundation
import Testing
import UIKit

@testable import SimMirrorKit

func testListing(pid: Int32 = 42, startedAt: String = "2026-09-17T08:00:00.000Z", active: Bool = true) -> Listing {
    Listing(
        protocolVersion: 1, sdkVersion: SimMirror.sdkVersion, deviceUDID: "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11",
        bundleID: "io.github.andrewkochulab.simmirror.tests", name: "Tests", pid: pid, port: 5000,
        secret: "q2Vn1c7yJx0mH4tR8bW3sK6pZ9dL5fA2gE7uY1oN3iC", active: active, startedAt: startedAt)
}

struct DiscoveryTests {
    @Test func theListingGoesInTheSharedFolderReadableByItsOwnerOnly() throws {
        let shared = try TemporaryFolder()
        let home = try TemporaryFolder()
        let writer = DiscoveryWriter(sharedFolder: shared.path, home: home.path)
        let listing = testListing()
        let path = try #require(writer.write(listing))
        let relative = "Library/Caches/SimMirror/apps/io.github.andrewkochulab.simmirror.tests.json"
        #expect(path == "\(shared.path)/\(relative)" && writer.path == path)
        #expect(shared.mode(of: relative) == 0o600 && shared.mode(of: "Library/Caches/SimMirror/apps") == 0o700)
        #expect(try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path))) == listing)
        var inactive = listing
        inactive.active = false
        #expect(writer.write(inactive) == path)
        #expect(
            try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path))).active == false)
        #expect(
            try FileManager.default.contentsOfDirectory(atPath: (path as NSString).deletingLastPathComponent).count == 1
        )
        writer.remove(inactive)
        #expect(!FileManager.default.fileExists(atPath: path) && writer.path == nil)
        writer.remove(inactive)
    }

    @Test func whenTheSharedFolderCannotBeWrittenTheAppsOwnCachesAre() throws {
        let home = try TemporaryFolder()
        let blocked = try TemporaryFolder()
        FileManager.default.createFile(atPath: "\(blocked.path)/Library", contents: Data())
        let writer = DiscoveryWriter(sharedFolder: blocked.path, home: home.path)
        let path = try #require(writer.write(testListing()))
        #expect(path.hasPrefix(home.path))
        #expect(DiscoveryWriter(sharedFolder: nil, home: "\(blocked.path)/Library").write(testListing()) == nil)
    }

    @Test func aListingANewerLaunchReplacedIsNotRemoved() throws {
        let shared = try TemporaryFolder()
        let writer = DiscoveryWriter(sharedFolder: shared.path, home: shared.path)
        let path = try #require(writer.write(testListing()))
        let newer = DiscoveryWriter(sharedFolder: shared.path, home: shared.path)
        newer.write(testListing(pid: 43, startedAt: "2026-09-17T09:00:00.000Z"))
        writer.remove(testListing())
        #expect(FileManager.default.fileExists(atPath: path))
        let sameProcess = DiscoveryWriter(sharedFolder: shared.path, home: shared.path)
        sameProcess.write(testListing(pid: 43, startedAt: "2026-09-17T10:00:00.000Z"))
        newer.remove(testListing(pid: 43, startedAt: "2026-09-17T09:00:00.000Z"))
        #expect(FileManager.default.fileExists(atPath: path))
        FileManager.default.createFile(atPath: path, contents: Data("not json".utf8))
        sameProcess.remove(testListing(pid: 43, startedAt: "2026-09-17T10:00:00.000Z"))
        #expect(FileManager.default.fileExists(atPath: path))
    }

    @Test func aListingThatCannotBeWrittenWholeIsNotLeftHalfWritten() throws {
        let folder = try TemporaryFolder()
        let live = FileSystem.live
        let path = "\(folder.path)/listing.json"
        FileManager.default.createFile(atPath: "\(path).\(getpid()).tmp", contents: Data())
        #expect(!live.writeAtomically(Data("{}".utf8), path))
        #expect(!live.writeAtomically(Data("{}".utf8), "\(folder.path)/missing/listing.json"))
        try FileManager.default.createDirectory(atPath: path, withIntermediateDirectories: false)
        try FileManager.default.removeItem(atPath: "\(path).\(getpid()).tmp")
        #expect(!live.writeAtomically(Data("{}".utf8), path))
        #expect(!FileManager.default.fileExists(atPath: "\(path).\(getpid()).tmp"))
        #expect(live.writeAtomically(Data(), "\(folder.path)/empty.json"))
        #expect(!live.makeFolder("/dev/null/nope"))
        #expect(live.read("\(folder.path)/nothing") == nil)
    }
}

/// An app host a test drives: in front or not, its windows, and the events it sends.
@MainActor
final class FakeHost: AppHost {
    var isActive = true
    var windows: [UIWindow] = []
    var keyboardFrame: CGRect?
    var handler: ((AppEvent) -> Void)?
    var stopped = 0

    func observe(_ handler: @escaping @MainActor (AppEvent) -> Void) { self.handler = handler }
    func stopObserving() {
        handler = nil
        stopped += 1
    }
}

final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var written: [String] = []
    var log: Log { Log { line in self.lock.withLock { self.written.append(line) } } }
    var all: [String] { lock.withLock { written } }
}

@MainActor
struct RuntimeTests {
    static let udid = "7A4C5B2E-9E2B-4C43-9F3A-2D0C3F0B6E11"

    private func process(_ folder: TemporaryFolder, changes: (inout ProcessContext) -> Void = { _ in })
        -> ProcessContext
    {
        var process = ProcessContext(
            environment: ["SIMULATOR_UDID": Self.udid, "SIMULATOR_SHARED_RESOURCES_DIRECTORY": folder.path],
            bundleID: "io.github.andrewkochulab.simmirror.tests", name: "Tests", pid: getpid(), home: folder.path,
            bundlePath: "/Apps/Tests.app", os: ProcessInfo.processInfo.operatingSystemVersion)
        changes(&process)
        return process
    }

    private func listing(at path: String?) throws -> Listing {
        let path = try #require(path)
        return try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    }

    @Test func startingListensWritesTheListingAndAnswersWithTheSecretOnly() async throws {
        let folder = try TemporaryFolder()
        let host = FakeHost()
        let label = UILabel(frame: CGRect(x: 20, y: 100, width: 200, height: 20))
        label.text = "Hello"
        host.windows = [testWindow(label)]
        let lines = Lines()
        let runtime = Runtime(
            host: host, process: process(folder), log: lines.log, now: { Date(timeIntervalSince1970: 0) },
            mainThreadTimeout: 30)
        runtime.start(SimMirror.Options(maxNodes: 50))
        runtime.start(SimMirror.Options())
        #expect(runtime.isRunning)
        let written = try listing(at: runtime.listingPath)
        #expect(written.deviceUDID == Self.udid && written.startedAt == "1970-01-01T00:00:00.000Z" && written.active)
        #expect(written.pid == getpid() && written.protocolVersion == 1 && written.name == "Tests")
        let answer = try #require(await RawClient.request(port: written.port, secret: written.secret))
        #expect(answer.status == 200)
        let hierarchy = try JSONDecoder().decode(Hierarchy.self, from: answer.body)
        #expect(hierarchy.windows.first?.nodes.map(\.label) == ["Hello"] && hierarchy.app.pid == getpid())
        #expect(try #require(await RawClient.request(port: written.port, secret: "wrong")).status == 401)
        host.isActive = false
        #expect(try #require(await RawClient.request(port: written.port, secret: written.secret)).status == 409)
        #expect(
            lines.all == ["SimMirror answers on 127.0.0.1:\(written.port); it said so in \(runtime.listingPath ?? "")"])
        #expect(!lines.all.joined().contains(written.secret))
        let path = runtime.listingPath
        runtime.stop()
        runtime.stop()
        #expect(!runtime.isRunning && runtime.listingPath == nil && host.stopped == 1)
        #expect(!FileManager.default.fileExists(atPath: path ?? ""))
        #expect(lines.all.last == "SimMirror stopped")
    }

    @Test func theListingFollowsTheAppInAndOutOfFrontAndGoesWhenItEnds() throws {
        let folder = try TemporaryFolder()
        let host = FakeHost()
        host.isActive = false
        let runtime = Runtime(host: host, process: process(folder), log: Lines().log)
        runtime.start(SimMirror.Options())
        #expect(try listing(at: runtime.listingPath).active == false)
        host.handler?(.active)
        #expect(try listing(at: runtime.listingPath).active == true)
        host.handler?(.inactive)
        #expect(try listing(at: runtime.listingPath).active == false)
        let path = runtime.listingPath
        host.handler?(.terminating)
        #expect(!runtime.isRunning && !FileManager.default.fileExists(atPath: path ?? ""))
        runtime.handle(.active)
    }

    @Test func itDoesNotStartWhereItShouldNotAndSaysWhy() throws {
        let folder = try TemporaryFolder()
        let refusals: [((inout ProcessContext) -> Void, String)] = [
            ({ $0.environment["XCODE_RUNNING_FOR_PREVIEWS"] = "1" }, "not in an Xcode preview"),
            ({ $0.bundlePath = "/Apps/Tests.app/PlugIns/Widget.appex" }, "not in an app extension"),
            ({ $0.environment["SIMULATOR_UDID"] = nil }, "SIMULATOR_UDID is not set, so this is not a simulator"),
            ({ $0.bundleID = nil }, "the app has no bundle identifier SimMirror can name its listing after"),
            ({ $0.bundleID = "bad/id" }, "the app has no bundle identifier SimMirror can name its listing after"),
        ]
        for (change, reason) in refusals {
            let lines = Lines()
            let runtime = Runtime(host: FakeHost(), process: process(folder, changes: change), log: lines.log)
            runtime.start(SimMirror.Options())
            #expect(!runtime.isRunning && lines.all == ["SimMirror does not start: \(reason)"])
        }
    }

    @Test func noRandomnessNoPortOrNowhereToWriteStopsItStarting() throws {
        let folder = try TemporaryFolder()
        let lines = Lines()
        Runtime(host: FakeHost(), process: process(folder), random: { _ in false }, log: lines.log).start(
            SimMirror.Options())
        let taken = LoopbackServer { _, _ in HTTPResponse(status: 200, body: Data()) }
        defer { taken.stop() }
        let port = try taken.start(port: 0)
        Runtime(host: FakeHost(), process: process(folder), log: lines.log).start(SimMirror.Options(port: port))
        let blocked = try TemporaryFolder()
        FileManager.default.createFile(atPath: "\(blocked.path)/Library", contents: Data())
        let nowhere = process(blocked) { $0.home = "\(blocked.path)/Library" }
        let runtime = Runtime(host: FakeHost(), process: nowhere, log: lines.log)
        runtime.start(SimMirror.Options())
        #expect(!runtime.isRunning)
        #expect(
            lines.all == [
                "SimMirror does not start: the system gave no random bytes for its secret",
                "SimMirror does not start: it could not listen on 127.0.0.1:\(port) (Address already in use)",
                "SimMirror does not start: it could not write where it listens under Library/Caches",
            ])
    }

    @Test func askingForDebugDataOnIOS26SetsSwiftUIsVariable() throws {
        let folder = try TemporaryFolder()
        let os26 = OperatingSystemVersion(majorVersion: 26, minorVersion: 5, patchVersion: 0)
        let runtime = Runtime(host: FakeHost(), process: process(folder) { $0.os = os26 }, log: Lines().log)
        runtime.start(SimMirror.Options(swiftUIDebugData: true))
        defer { runtime.stop() }
        #expect(String(cString: getenv(DebugDataPolicy.variable)) == DebugDataPolicy.properties)
    }

    @Test func aMainThreadThatDoesNotAnswerInTimeIsBusy() async {
        let result = await Task.detached {
            Runtime.onMain(timeout: 0.05) {
                Thread.sleep(forTimeInterval: 0.3)
                return .inactive
            }
        }.value
        #expect(result == .busy)
        let nothing = await Task.detached { Runtime.onMain(timeout: 1) { nil } }.value
        #expect(nothing == .busy)
        let answered = await Task.detached { Runtime.onMain(timeout: 1) { .inactive } }.value
        #expect(answered == .inactive)
        #expect(Runtime.timestamp(Date(timeIntervalSince1970: 1.5)) == "1970-01-01T00:00:01.500Z")
    }

    @Test func theProcessIsDescribedFromWhatItRunsWith() {
        let current = ProcessContext.current()
        #expect(current.pid == getpid() && current.home == NSHomeDirectory() && !current.name.isEmpty)
        #expect(current.deviceUDID == ProcessInfo.processInfo.environment["SIMULATOR_UDID"])
        #expect(current.sharedFolder == ProcessInfo.processInfo.environment["SIMULATOR_SHARED_RESOURCES_DIRECTORY"])
        #expect(
            ProcessContext.name(info: ["CFBundleDisplayName": "Shown", "CFBundleName": "Bundle"], process: "p")
                == "Shown")
        #expect(ProcessContext.name(info: ["CFBundleName": "Bundle"], process: "p") == "Bundle")
        #expect(ProcessContext.name(info: nil, process: "p") == "p")
    }
}

@MainActor
struct AppHostTests {
    @Test func withoutAnApplicationTheAppIsNotInFrontAndHasNoWindows() {
        let host = UIKitAppHost(center: NotificationCenter(), application: { nil })
        #expect(!host.isActive && host.windows.isEmpty && host.keyboardFrame == nil)
        #expect(UIKitAppHost.shared() == nil)
    }

    @Test func eventsAndTheKeyboardAreHeardUntilObservingStops() {
        let center = NotificationCenter()
        let host = UIKitAppHost(center: center, application: { nil })
        var heard: [AppEvent] = []
        host.observe { heard.append($0) }
        host.observe { heard.append($0) }
        center.post(name: UIApplication.didBecomeActiveNotification, object: nil)
        center.post(name: UIApplication.didEnterBackgroundNotification, object: nil)
        center.post(name: UIApplication.willTerminateNotification, object: nil)
        #expect(heard == [.active, .inactive, .terminating])
        let screen = UIScreen.main.bounds
        let keyboard = CGRect(x: 0, y: screen.height - 300, width: screen.width, height: 300)
        center.post(
            name: UIResponder.keyboardWillChangeFrameNotification, object: nil,
            userInfo: [UIResponder.keyboardFrameEndUserInfoKey: keyboard])
        #expect(host.keyboardFrame == keyboard)
        center.post(
            name: UIResponder.keyboardWillChangeFrameNotification, object: nil,
            userInfo: [UIResponder.keyboardFrameEndUserInfoKey: keyboard.offsetBy(dx: 0, dy: 300)])
        #expect(host.keyboardFrame == nil)
        center.post(
            name: UIResponder.keyboardWillChangeFrameNotification, object: nil,
            userInfo: [UIResponder.keyboardFrameEndUserInfoKey: keyboard])
        center.post(name: UIResponder.keyboardWillHideNotification, object: nil)
        #expect(host.keyboardFrame == nil)
        host.stopObserving()
        center.post(name: UIApplication.didBecomeActiveNotification, object: nil)
        #expect(heard.count == 3)
    }
}

@MainActor
struct PublicAPITests {
    @Test func optionsDefaultToAnyPortThreeThousandViewsValuesAndNoDebugData() {
        let options = SimMirror.Options()
        #expect(options == SimMirror.Options(port: 0, maxNodes: 3000, redactValues: false, swiftUIDebugData: false))
        #expect(SimMirror.Kind.navigationBar.rawValue == "navigation_bar" && SimMirror.Kind.allCases.count == 21)
        #expect(SimMirror.sdkVersion.range(of: #"^\d+\.\d+\.\d+"#, options: .regularExpression) != nil)
    }

    struct Nothing: ViewDescribing {
        func describe(_ view: UIView) -> ViewDescription? { nil }
    }

    @Test func theSharedRuntimeStartsAndStopsThroughThePublicCalls() {
        SimMirror.register(describer: Nothing())
        #expect(Runtime.shared.describers.custom.count == 1)
        SimMirror.start()
        #expect(SimMirror.isRunning)
        SimMirror.stop()
        #expect(!SimMirror.isRunning)
        #expect(ViewDescription.hidden.isHidden && ViewDescription.hidden.interactive == false)
    }
}

// SPDX-License-Identifier: Apache-2.0
import AppKit
import CoreGraphics
import Foundation
import HelperCore
import ScreenCaptureKit

/// One process and one selected window. Framework calls run on one queue; capture callbacks have their own queue.
public final class MacWindowDevice: Device, @unchecked Sendable {
    public let coreSimulator: String? = nil
    public let source: String? = "mac-window"
    private let options: MacWindowOptions
    private let application: NSRunningApplication
    private let capture: MacWindowCapture
    private let feed: ScreenFeed
    private let accessibility: MacAccessibility?
    private let queue = DispatchQueue(label: "sim-mirror.mac.accessibility")
    public var alive: Bool { !application.isTerminated }

    public static func initialize() { _ = NSApplication.shared }

    public init(options: MacWindowOptions) async throws {
        guard let app = NSRunningApplication(processIdentifier: options.pid), !app.isTerminated else {
            throw HelperFailure("the target app process has ended", status: 409)
        }
        guard !app.isHidden else { throw HelperFailure("the app is hidden; show its windows before connecting", status: 409) }
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperFailure("allow this helper in Privacy & Security > Screen Recording", status: 403)
        }
        self.options = options
        application = app
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        let windows = content.windows.filter { $0.owningApplication?.processID == options.pid && $0.frame.width > 1 && $0.frame.height > 1 }
        let chosen = options.windowID.flatMap { id in windows.first { $0.windowID == id } }
            ?? (options.windowID == nil ? windows.sorted {
                if ($0.windowLayer == 0) != ($1.windowLayer == 0) { return $0.windowLayer == 0 }
                return $0.frame.width*$0.frame.height > $1.frame.width*$1.frame.height
            }.first : nil)
        guard let chosen else { throw HelperFailure("no window of this process matches; restore or open an app window", status: 409) }
        capture = try MacWindowCapture(window: chosen, pid: options.pid)
        feed = ScreenFeed(source: capture, pictures: Pictures(), idleKeyFrames: true, firstPictureS: 5, log: Log())
        accessibility = options.accessibility ? try? MacAccessibility(pid: options.pid) : nil
        try await capture.start()
    }
    private func requireAlive() throws {
        guard alive else { throw HelperFailure("the target app process has ended", status: 409) }
    }
    private func bounds() throws -> CGRect {
        try requireAlive()
        let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, capture.window.windowID) as? [[String:Any]] ?? []
        guard let owned = rows.first(where: { ($0[kCGWindowOwnerPID as String] as? Int) == Int(options.pid) }),
              let raw = owned[kCGWindowBounds as String] as? [String:Any],
              let bounds = CGRect(dictionaryRepresentation:raw as CFDictionary) else {
            throw HelperFailure("the selected app window has ended; choose another window", status:409)
        }
        guard abs(bounds.width-capture.window.frame.width) < 1,
              abs(bounds.height-capture.window.frame.height) < 1 else {
            throw HelperFailure("the app window was resized; select it again to reconnect capture and refresh refs", status:409)
        }
        return bounds
    }
    public func screen() throws -> ScreenGeometry { _ = try bounds(); return capture.geometry }
    public func screenshot(_ request: ScreenshotRequest) async throws -> JPEG {
        try feed.screenshot(request, screen: screen())
    }
    public func stream(_ settings: StreamSettings) throws -> AsyncThrowingStream<Data, Error> {
        try requireAlive(); return try feed.stream(settings)
    }
    public func tree() async throws -> AXTree {
        let bounds = try bounds()
        return try queue.sync { try reader().tree(bounds: bounds) }
    }
    private func reader() throws -> MacAccessibility {
        try requireAlive()
        guard let accessibility else { throw HelperFailure("allow the helper in Privacy & Security > Accessibility", status: 403) }
        return accessibility
    }
    public func elements(at points: [ScreenPoint]) async throws -> [AXNode?] {
        let bounds = try bounds()
        return try queue.sync {
            let reader = try reader()
            return points.map { point in
                (try? reader.hit(x: point.x,y: point.y,bounds: bounds)).map { reader.node($0,origin:bounds.origin) }
            }
        }
    }
    public func input() throws -> InputDriver {
        throw HelperFailure("macOS input uses app-scoped semantic actions, not simulator HID", status: 409)
    }
    public func rotate(_ orientation: InterfaceOrientation) throws {
        throw HelperFailure("macOS app windows cannot be rotated", status: 409)
    }
    public func mac(_ action: MacAction) async throws -> JSONValue {
        try action.validate()
        try requireAlive()
        if action.kind == .info {
            let content = try await SCShareableContent.excludingDesktopWindows(true,onScreenWindowsOnly:false)
            return .object(["pid":.int(Int(options.pid)), "window_id":.int(Int(capture.window.windowID)),
                            "windows":.array(content.windows.filter { $0.owningApplication?.processID == options.pid && $0.frame.width > 1 && $0.frame.height > 1 }.map {
                                .object(["id":.int(Int($0.windowID)),"title":.string($0.title ?? ""),"on_screen":.bool($0.isOnScreen)])
                            })])
        }
        if action.kind == .window { throw HelperFailure("reconnect with the chosen window id",status:409) }
        let bounds = try bounds()
        try queue.sync { try reader().act(action,bounds:bounds) }
        return .object([:])
    }
    public func warmed() async {}
    public func close() { capture.close(); queue.sync { accessibility?.close() } }
}

/// Running applications offered as targets. This is metadata only; no accessibility tree is read here.
public enum MacApplications {
    public static func list() -> Data {
        let apps: [[String: Any]] = NSWorkspace.shared.runningApplications.compactMap { app in
            guard app.activationPolicy == .regular, let id = app.bundleIdentifier, let path = app.bundleURL?.path else { return nil }
            return ["bundle_id": id, "name": app.localizedName ?? id, "path": path, "pid": app.processIdentifier]
        }
        return (try? JSONSerialization.data(withJSONObject: apps,options:[.sortedKeys])) ?? Data("[]".utf8)
    }
}

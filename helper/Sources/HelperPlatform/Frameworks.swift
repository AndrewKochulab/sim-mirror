// SPDX-License-Identifier: Apache-2.0
import Foundation
import HelperCore
import ObjCGuard

/// Apple's private simulator frameworks, loaded at run time from the Xcode SimMirror names.
///
/// Nothing links against them: they are opened with `dlopen` and reached through the Objective-C runtime, so one
/// binary works whichever Xcode is chosen. CoreSimulator is installed system-wide by the newest Xcode; SimulatorKit lives
/// inside each Xcode, and Xcode 27 moved it from `Developer/Library/PrivateFrameworks` to `SharedFrameworks`.
public enum SimulatorFrameworks {
    public static let coreSimulator = "/Library/Developer/PrivateFrameworks/CoreSimulator.framework/CoreSimulator"
    public static let accessibilityTranslation =
        "/System/Library/PrivateFrameworks/AccessibilityPlatformTranslation.framework/AccessibilityPlatformTranslation"

    /// The developer folder this process uses: ``DEVELOPER_DIR``, else the one ``xcode-select`` points at.
    public static func developerDir(environment: [String: String] = ProcessInfo.processInfo.environment) -> String {
        if let named = environment["DEVELOPER_DIR"], !named.isEmpty { return named }
        let link = "/var/db/xcode_select_link"
        if let target = try? FileManager.default.destinationOfSymbolicLink(atPath: link) { return target }
        return "/Applications/Xcode.app/Contents/Developer"
    }

    /// SimulatorKit's binary in each place an Xcode keeps it, newest first.
    public static func simulatorKitCandidates(developerDir: String) -> [String] {
        let developer = URL(fileURLWithPath: developerDir)
        return [
            developer.deletingLastPathComponent().appendingPathComponent("SharedFrameworks/SimulatorKit.framework/SimulatorKit").path,
            developer.appendingPathComponent("Library/PrivateFrameworks/SimulatorKit.framework/SimulatorKit").path,
        ]
    }

    private static let lock = NSLock()
    private static var loaded: String?

    /// Load CoreSimulator and SimulatorKit, answering CoreSimulator's version. Loading twice is loading once.
    @discardableResult
    public static func load(developerDir: String) throws -> String? {
        lock.lock()
        defer { lock.unlock() }
        if let loaded { return loaded }
        guard dlopen(coreSimulator, RTLD_NOW) != nil else {
            throw HelperFailure("CoreSimulator could not be loaded from \(coreSimulator): \(lastError())", status: 409)
        }
        guard simulatorKitCandidates(developerDir: developerDir).contains(where: { dlopen($0, RTLD_NOW) != nil }) else {
            throw HelperFailure("SimulatorKit is not in the Xcode at \(developerDir); name another with device.developer_dir", status: 409)
        }
        let version = NSClassFromString("SimDevice").flatMap { Bundle(for: $0).infoDictionary?["CFBundleVersion"] as? String }
        loaded = version ?? ""
        return version
    }

    static func lastError() -> String {
        dlerror().map { String(cString: $0) } ?? "unknown"
    }
}

/// Objective-C calls that can raise, made so a raised exception is an error Swift can catch.
enum ObjC {
    static func guarded<T>(_ what: String, _ body: () -> T) throws -> T {
        var result: T?
        if let exception = SMGuard({ result = body() }) {
            throw HelperFailure("\(what) raised \(exception.name.rawValue): \(exception.reason ?? "no reason")")
        }
        return result!
    }

    static func object(_ target: NSObject, _ selector: String) -> NSObject? {
        let sel = NSSelectorFromString(selector)
        guard target.responds(to: sel) else { return nil }
        return target.perform(sel)?.takeUnretainedValue() as? NSObject
    }

    static func anyObject(_ target: NSObject, _ selector: String) -> AnyObject? {
        let sel = NSSelectorFromString(selector)
        guard target.responds(to: sel) else { return nil }
        return target.perform(sel)?.takeUnretainedValue()
    }

    static func symbol<T>(_ name: String, as type: T.Type) -> T? {
        guard let pointer = dlsym(UnsafeMutableRawPointer(bitPattern: -2), name) else { return nil }
        return unsafeBitCast(pointer, to: type)
    }
}

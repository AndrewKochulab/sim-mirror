// SPDX-License-Identifier: Apache-2.0
import ApplicationServices
import CoreGraphics
import Foundation
import HelperCore
import ScreenCaptureKit

/// Checks this process's grants before live app-scoped work. No request API is called, and window enumeration is
/// attempted only after Screen Recording is already allowed. The report contains counts, never window titles.
public enum MacPermissions {
    public static func check() async -> SelfCheckReport {
        let trusted = AXIsProcessTrusted()
        var parts: [SelfCheckReport.Part] = [
            .init(
                name: "accessibility", ok: trusted,
                detail: trusted ? "the helper is trusted" : "allow the helper in System Settings > Privacy & Security > Accessibility"
            )
        ]
        guard CGPreflightScreenCaptureAccess() else {
            parts.append(.init(
                name: "screen_recording", ok: false,
                detail: "allow the helper in System Settings > Privacy & Security > Screen Recording"
            ))
            return SelfCheckReport(parts: parts)
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
            parts.append(.init(name: "screen_recording", ok: true, detail: "ScreenCaptureKit listed \(content.windows.count) windows"))
        } catch {
            parts.append(.init(name: "screen_recording", ok: false, detail: HelperFailure.from(error).message))
        }
        return SelfCheckReport(parts: parts)
    }
}

// SPDX-License-Identifier: Apache-2.0
#if DEBUG && os(macOS)
    import AppKit

    /// SwiftUI's accessibility nodes implement the public selectors without declaring NSAccessibility conformance.
    /// Resolve those selectors on objects already obtained from this app's views; never message another process.
    @MainActor
    struct MacAccess {
        let object: NSObject

        init?(_ value: Any?) {
            guard let object = value as? NSObject else { return nil }
            self.object = object
        }

        func value(_ name: String) -> Any? {
            let selector = NSSelectorFromString(name)
            guard object.responds(to: selector) else { return nil }
            return object.perform(selector)?.takeUnretainedValue()
        }

        func flag(_ name: String) -> Bool? {
            let selector = NSSelectorFromString(name)
            guard object.responds(to: selector) else { return nil }
            let method = unsafeBitCast(
                object.method(for: selector), to: (@convention(c) (AnyObject, Selector) -> Bool).self)
            return method(object, selector)
        }

        var frame: CGRect? {
            let selector = NSSelectorFromString("accessibilityFrame")
            guard object.responds(to: selector) else { return nil }
            let method = unsafeBitCast(
                object.method(for: selector), to: (@convention(c) (AnyObject, Selector) -> CGRect).self)
            return method(object, selector)
        }

        var children: [AnyObject] { (value("accessibilityChildren") as? [Any] ?? []).map { $0 as AnyObject } }
        var role: String { value("accessibilityRole") as? String ?? "" }
        var subrole: String { value("accessibilitySubrole") as? String ?? "" }
        var label: String? { value("accessibilityLabel") as? String }
        var identifier: String? { value("accessibilityIdentifier") as? String }
        var enabled: Bool { flag("isAccessibilityEnabled") ?? true }
        var text: String? {
            let raw = value("accessibilityValue")
            return raw as? String ?? (raw as? NSNumber)?.stringValue
        }
        func press() -> Bool { flag("accessibilityPerformPress") == true }
    }
#endif

// SPDX-License-Identifier: Apache-2.0
import AppKit
import ApplicationServices
import Foundation
import HelperCore

/// Accessibility rooted at one PID. Every returned element is checked again before reading or acting on it.
final class MacAccessibility {
    let pid: Int32
    let app: AXUIElement
    private var restored: [(String, CFTypeRef)] = []
    private var textTarget: AXUIElement?

    init(pid: Int32) throws {
        guard pid > 1, pid != getpid(), AXIsProcessTrusted() else {
            throw HelperFailure("allow this helper in Privacy & Security > Accessibility", status: 403)
        }
        self.pid = pid
        app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 1)
        // Some Chromium hosts only publish their tree while assistive technology is attached. Never overwrite a
        // value we cannot restore, and never turn off an attribute somebody else had already enabled.
        for name in ["AXManualAccessibility", "AXEnhancedUserInterface"] {
            if let old = value(app, name), CFGetTypeID(old) == CFBooleanGetTypeID(), !(old as! Bool),
               AXUIElementSetAttributeValue(app, name as CFString, kCFBooleanTrue) == .success {
                restored.append((name, old))
            }
        }
    }

    func close() {
        for (name, old) in restored { _ = AXUIElementSetAttributeValue(app, name as CFString, old) }
        restored.removeAll()
    }

    func owns(_ element: AXUIElement) -> Bool {
        var owner: pid_t = 0
        return AXUIElementGetPid(element, &owner) == .success && owner == pid
    }

    func value(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        guard owns(element) else { return nil }
        var result: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success ? result : nil
    }
    func children(_ element: AXUIElement, _ name: String = "AXChildren") -> [AXUIElement] {
        (value(element, name) as? [AXUIElement] ?? []).filter(owns)
    }
    func element(_ parent: AXUIElement, _ name: String) -> AXUIElement? {
        guard let raw = value(parent, name), CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
        let element = unsafeBitCast(raw, to: AXUIElement.self)
        return owns(element) ? element : nil
    }
    func frame(_ element: AXUIElement) -> CGRect? {
        guard let position = value(element, "AXPosition"), let size = value(element, "AXSize"),
              CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero; var extent = CGSize.zero
        guard AXValueGetValue(unsafeBitCast(position, to: AXValue.self), .cgPoint, &point),
              AXValueGetValue(unsafeBitCast(size, to: AXValue.self), .cgSize, &extent) else { return nil }
        return CGRect(origin: point, size: extent)
    }
    func window(at bounds: CGRect) throws -> AXUIElement {
        guard let found = children(app, "AXWindows").first(where: {
            guard let rect = frame($0) else { return false }
            return abs(rect.minX-bounds.minX) < 3 && abs(rect.minY-bounds.minY) < 3 && abs(rect.width-bounds.width) < 3
        }) else { throw HelperFailure("the selected window is not in this process's accessibility tree", status: 409) }
        return found
    }
    func node(_ element: AXUIElement, origin: CGPoint, children: [AXNode] = []) -> AXNode {
        let rect = frame(element) ?? .zero
        let role = value(element, "AXRole") as? String
        let subrole = value(element, "AXSubrole") as? String
        var content: AccessibilityValue?
        if subrole != "AXSecureTextField", role != "AXSecureTextField" {
            if let text = value(element, "AXValue") as? String { content = .text(String(text.prefix(2000))) }
            else if let number = value(element, "AXValue") as? NSNumber { content = .number(number.doubleValue) }
        }
        let dom = value(element, "AXDOMIdentifier") as? String
        let description = value(element, "AXDescription") as? String
        let title = value(element, "AXTitle") as? String
        let text = ["AXStaticText", "AXHeading", "AXListMarker"].contains(role ?? "") ? value(element, "AXValue") as? String : nil
        let placeholder = ["AXTextField", "AXTextArea", "AXSecureTextField"].contains(role ?? "") ? value(element, "AXPlaceholderValue") as? String : nil
        let label = [description, title, text, placeholder].compactMap { $0 }.first { !$0.isEmpty }
        return AXNode(role: role, subrole: subrole, label: label,
                      title: value(element, "AXTitle") as? String,
                      identifier: dom.flatMap { $0.isEmpty ? nil : $0 } ?? value(element, "AXIdentifier") as? String,
                      value: content, frame: Rect(x: rect.minX-origin.x, y: rect.minY-origin.y, width: rect.width, height: rect.height),
                      enabled: value(element, "AXEnabled") as? Bool ?? true, children: children)
    }
    func tree(bounds: CGRect) throws -> AXTree {
        var seen = Set<AXUIElement>()
        func walk(_ element: AXUIElement, _ depth: Int, clip: CGRect) -> AXNode? {
            guard owns(element), depth < 40, seen.count < 3000, seen.insert(element).inserted else { return nil }
            var result = node(element, origin: bounds.origin)
            let raw = frame(element) ?? .zero
            let visible = raw.isEmpty ? clip : raw.intersection(clip)
            guard !visible.isNull, !visible.isEmpty else { return nil }
            if !result.isSecure { result.children = children(element).compactMap { walk($0, depth+1, clip: visible) } }
            if raw.isEmpty, result.children.isEmpty { return nil }
            result.frame = Rect(x: visible.minX-bounds.minX, y: visible.minY-bounds.minY, width: visible.width, height: visible.height)
            return result
        }
        let root = try window(at: bounds)
        return AXTree(root: walk(root,0,clip:bounds) ?? node(root,origin:bounds.origin), orientation: .portrait)
    }
    func hit(x: Double, y: Double, bounds: CGRect) throws -> AXUIElement {
        guard x.isFinite, y.isFinite, x >= 0, y >= 0, x < bounds.width, y < bounds.height else {
            throw HelperFailure("the point is outside the selected app window", status: 403)
        }
        let root = try window(at: bounds)
        let point = CGPoint(x: bounds.minX+x, y: bounds.minY+y)
        let found = MacHitSearch.deepest(root, x: point.x, y: point.y, bounds: { element in
            self.frame(element).map { Rect(x: $0.minX, y: $0.minY, width: $0.width, height: $0.height) }
        }, children: { self.children($0) })
        guard let found, found != root else {
            throw HelperFailure("there is no element of this app at that point", status: 409)
        }
        return found
    }
    func perform(_ element: AXUIElement, _ action: String) throws {
        guard owns(element) else { throw HelperFailure("the element belongs to another process", status: 403) }
        var listed: CFArray?
        AXUIElementCopyActionNames(element, &listed)
        guard (listed as? [String] ?? []).contains(action) else {
            throw HelperFailure("this element does not expose \(action); link SimMirrorKit or the debug web bridge", status: 409)
        }
        guard AXUIElementPerformAction(element, action as CFString) == .success else {
            throw HelperFailure("the app refused \(action)", status: 409)
        }
    }
    func act(_ action: MacAction, bounds: CGRect) throws {
        switch action.kind {
        case .tap, .rightClick:
            if action.kind == .rightClick, NSRunningApplication(processIdentifier: pid)?.isActive != true {
                throw HelperFailure("context-menu tracking can take focus; use an app menu title path", status: 409)
            }
            var target: AXUIElement? = try hit(x: action.x!, y: action.y!, bounds: bounds)
            let verb = action.kind == .tap ? "AXPress" : "AXShowMenu"
            for _ in 0..<40 {
                guard let here = target, owns(here), let rect = frame(here), rect.intersects(bounds) else { break }
                let role = value(here, "AXRole") as? String ?? ""
                if action.kind == .tap, ["AXTextField", "AXTextArea", "AXSecureTextField"].contains(role) {
                    textTarget = here // Text can be set without changing global keyboard focus.
                    return
                }
                var actions: CFArray?
                AXUIElementCopyActionNames(here, &actions)
                if (actions as? [String] ?? []).contains(verb) {
                    if (actions as? [String] ?? []).contains("AXScrollToVisible") { try perform(here,"AXScrollToVisible") }
                    try perform(here,verb)
                    return
                }
                target = element(here,"AXParent")
            }
            throw HelperFailure("this view exposes no \(verb); link SimMirrorKit or the debug web bridge", status: 409)
        case .type:
            guard let target = textTarget ?? element(app,"AXFocusedUIElement"), owns(target),
                  let rect = frame(target), rect.intersects(bounds) else {
                throw HelperFailure("choose a text field in this app window before typing", status: 409)
            }
            var attribute = "AXValue"
            var replacement = action.text!
            var selectedSettable: DarwinBoolean = false
            if action.clear != true {
                if AXUIElementIsAttributeSettable(target,"AXSelectedText" as CFString,&selectedSettable) == .success, selectedSettable.boolValue {
                    attribute = "AXSelectedText"
                } else if value(target,"AXSubrole") as? String != "AXSecureTextField",
                          let previous = value(target,"AXValue") as? String {
                    replacement = previous + replacement
                } else {
                    throw HelperFailure("this field cannot append text through accessibility; use clear or the debug SDK", status:409)
                }
            }
            var settable: DarwinBoolean = false
            guard AXUIElementIsAttributeSettable(target,attribute as CFString,&settable) == .success, settable.boolValue,
                  AXUIElementSetAttributeValue(target,attribute as CFString,replacement as CFString) == .success else {
                throw HelperFailure("this field does not accept AXValue; link SimMirrorKit or the debug web bridge", status: 409)
            }
            if attribute == "AXValue", !node(target,origin:bounds.origin).isSecure {
                for _ in 0..<20 {
                    if value(target,"AXValue") as? String == replacement { return }
                    Thread.sleep(forTimeInterval: 0.01)
                }
                throw HelperFailure("the app accepted AXValue but did not expose the requested text; use the debug bridge and inspect before retrying", status:409)
            }
        case .scroll:
            var target: AXUIElement? = try hit(x:action.x!,y:action.y!,bounds:bounds)
            let verb = abs(action.dy!) >= abs(action.dx!) ? (action.dy! >= 0 ? "AXScrollDownByPage" : "AXScrollUpByPage") : (action.dx! >= 0 ? "AXScrollRightByPage" : "AXScrollLeftByPage")
            for _ in 0..<40 {
                guard let here = target, owns(here) else { break }
                var actions: CFArray?
                AXUIElementCopyActionNames(here,&actions)
                if (actions as? [String] ?? []).contains(verb) {
                    let result = AXUIElementPerformAction(here,verb as CFString)
                    if result == .success { return }
                    if result != .actionUnsupported && result != .notImplemented {
                        throw HelperFailure("the app refused its accessibility scroll action",status:409)
                    }
                }
                let horizontal = abs(action.dx!) > abs(action.dy!)
                if let bar = element(here, horizontal ? "AXHorizontalScrollBar" : "AXVerticalScrollBar") {
                    let delta = horizontal ? action.dx! : action.dy!
                    if delta == 0 { return }
                    var settable: DarwinBoolean = false
                    if let old = value(bar,"AXValue") as? NSNumber,
                       AXUIElementIsAttributeSettable(bar,"AXValue" as CFString,&settable) == .success, settable.boolValue {
                        let low = (value(bar,"AXMinValue") as? NSNumber)?.doubleValue ?? 0
                        let high = (value(bar,"AXMaxValue") as? NSNumber)?.doubleValue ?? 1
                        let next = min(high,max(low,old.doubleValue + (delta > 0 ? 0.2 : -0.2)*(high-low)))
                        guard AXUIElementSetAttributeValue(bar,"AXValue" as CFString,NSNumber(value:next)) == .success else {
                            throw HelperFailure("the app refused its accessibility scrollbar",status:409)
                        }
                        return
                    }
                }
                target = element(here,"AXParent")
            }
            throw HelperFailure("this view exposes no accessibility scroll action; link SimMirrorKit or the debug web bridge", status: 409)
        case .menu:
            guard let path = action.path, !["Apple", "Services"].contains(path[0]), let bar = element(app,"AXMenuBar") else {
                throw HelperFailure("only this app's own menu items can be used", status: 403)
            }
            var current = bar
            for title in path {
                var candidates = children(current)
                if candidates.count == 1, value(candidates[0],"AXRole") as? String == "AXMenu" { candidates = children(candidates[0]) }
                guard let item = candidates.first(where: { value($0,"AXTitle") as? String == title }) else {
                    throw HelperFailure("that menu path is not exposed by this app", status: 409)
                }
                current = item
            }
            guard children(current).isEmpty else {
                throw HelperFailure("choose a leaf menu action; menu tracking can take focus", status:409)
            }
            try perform(current,"AXPress")
        case .keys, .hover:
            throw HelperFailure("\(action.kind.rawValue) needs in-process input; this accessibility session never posts global events", status: 409)
        case .info, .window: break
        }
    }
}

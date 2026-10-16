// SPDX-License-Identifier: Apache-2.0
//
// The translator bridge follows facebook/idb's FBSimulatorControl (MIT) and EvanBacon/serve-sim (Apache-2.0); see
// THIRD_PARTY_LICENSES.md.
import AppKit
import Foundation
import HelperCore

/// A device's accessibility tree, read on the Mac through Apple's AccessibilityPlatformTranslation.
///
/// The translator turns the simulator's accessibility into macOS accessibility elements. Each attribute it needs it asks
/// for through its delegate, which forwards the request to the device over CoreSimulator and waits for the answer. The
/// translator is one per process and not safe to use from two threads, so every read runs on one queue.
final class AccessibilityReader: NSObject, @unchecked Sendable {
    static let queue = DispatchQueue(label: "sim-mirror.accessibility", qos: .userInitiated)
    /// How long one attribute may take before it is read as nothing.
    static let requestTimeout = 5.0
    /// The deepest and the most elements a read walks: a screen deeper or larger than this is not one a person uses.
    static let maxDepth = 80
    static let maxElements = 2_000

    private let device: SimDeviceHandle
    private let replies = DispatchQueue(label: "sim-mirror.accessibility.replies")
    private let translator: NSObject

    init(device: SimDeviceHandle) throws {
        guard dlopen(SimulatorFrameworks.accessibilityTranslation, RTLD_NOW) != nil,
            let translatorClass = NSClassFromString("AXPTranslator") as? NSObject.Type,
            let translator = translatorClass.perform(NSSelectorFromString("sharedInstance"))?.takeUnretainedValue() as? NSObject
        else { throw HelperFailure("AccessibilityPlatformTranslation is not on this Mac") }
        guard device.object.responds(to: NSSelectorFromString("sendAccessibilityRequestAsync:completionQueue:completionHandler:")) else {
            throw HelperFailure("this CoreSimulator cannot send accessibility requests")
        }
        self.device = device
        self.translator = translator
        super.init()
        try ObjC.guarded("setting up the accessibility translator") {
            translator.setValue(self, forKey: "bridgeTokenDelegate")
            translator.setValue(true, forKey: "supportsDelegateTokens")
            translator.setValue(true, forKey: "accessibilityEnabled")
        }
    }

    /// The frontmost application's tree, and which way its interface is turned on `screen`.
    func tree(screen: ScreenGeometry) async throws -> AXTree {
        try await withCheckedThrowingContinuation { continuation in
            Self.queue.async {
                continuation.resume(with: Result { try self.read(screen: screen) })
            }
        }
    }

    private func read(screen: ScreenGeometry) throws -> AXTree {
        let token = UUID().uuidString
        typealias Frontmost = @convention(c) (AnyObject, Selector, UInt32, NSString) -> AnyObject?
        typealias MacElement = @convention(c) (AnyObject, Selector, AnyObject) -> AnyObject?
        let frontmostSelector = NSSelectorFromString("frontmostApplicationWithDisplayId:bridgeDelegateToken:")
        let macSelector = NSSelectorFromString("macPlatformElementFromTranslation:")
        let root = try ObjC.guarded("reading the frontmost application") { () -> NSAccessibilityElement? in
            guard let frontmost = translator.method(for: frontmostSelector), let convert = translator.method(for: macSelector),
                let translation = unsafeBitCast(frontmost, to: Frontmost.self)(translator, frontmostSelector, 0, token as NSString) as? NSObject
            else { return nil }
            translation.setValue(token, forKey: "bridgeDelegateToken")
            return unsafeBitCast(convert, to: MacElement.self)(translator, macSelector, translation) as? NSAccessibilityElement
        }
        guard let root else { throw HelperFailure("the simulator has no frontmost application to read yet", status: 503) }
        var budget = Self.maxElements
        let tree = try ObjC.guarded("reading the accessibility tree") { node(root, token: token, depth: 0, budget: &budget) }
        let orientation = try ObjC.guarded("finding which way the interface is turned") {
            InterfaceOrientation.find(tree, screen: screen) { x, y in hit(x: x, y: y, token: token) }
        }
        return AXTree(root: tree, orientation: orientation)
    }

    /// The frame, in the interface's points, of the element at a point of the screen in its portrait points; nil when
    /// nothing is there.
    ///
    /// The translator's hit test takes the screen's own points whichever way the interface is turned, and answers an
    /// element whose frame is the interface's: what tells the two apart.
    private func hit(x: Double, y: Double, token: String) -> Rect? {
        typealias AtPoint = @convention(c) (AnyObject, Selector, CGPoint, UInt32, NSString) -> AnyObject?
        let selector = NSSelectorFromString("objectAtPoint:displayId:bridgeDelegateToken:")
        let macSelector = NSSelectorFromString("macPlatformElementFromTranslation:")
        guard let method = translator.method(for: selector),
            let translation = unsafeBitCast(method, to: AtPoint.self)(translator, selector, CGPoint(x: x, y: y), 0, token as NSString) as? NSObject
        else { return nil }
        translation.setValue(token, forKey: "bridgeDelegateToken")
        guard let element = translator.perform(macSelector, with: translation)?.takeUnretainedValue() as? NSAccessibilityElement else {
            return nil
        }
        let frame = element.accessibilityFrame()
        return Rect(x: frame.origin.x, y: frame.origin.y, width: frame.width, height: frame.height)
    }

    private func node(_ element: NSAccessibilityElement, token: String, depth: Int, budget: inout Int) -> AXNode {
        budget -= 1
        let translation = element.value(forKey: "translation") as? NSObject
        translation?.setValue(token, forKey: "bridgeDelegateToken")
        var children: [AXNode] = []
        if depth < Self.maxDepth {
            for child in element.accessibilityChildren() ?? [] {
                guard budget > 0 else { break }
                if let child = child as? NSAccessibilityElement {
                    children.append(node(child, token: token, depth: depth + 1, budget: &budget))
                }
            }
        }
        let frame = element.accessibilityFrame()
        let role = element.accessibilityRole()?.rawValue
        return AXNode(
            role: role,
            subrole: element.accessibilitySubrole()?.rawValue,
            roleDescription: role == AXNode.groupRole ? element.accessibilityRoleDescription() : nil,
            label: element.accessibilityLabel(),
            title: element.accessibilityTitle(),
            identifier: element.accessibilityIdentifier(),
            value: Self.value(element.accessibilityValue()),
            frame: Rect(x: frame.origin.x, y: frame.origin.y, width: frame.width, height: frame.height),
            traits: Self.traits(element),
            enabled: element.isAccessibilityEnabled(),
            children: children
        )
    }

    static func value(_ raw: Any?) -> AccessibilityValue? {
        switch raw {
        case let text as String: return .text(text)
        case let text as NSAttributedString: return .text(text.string)
        case let number as NSNumber: return .number(number.doubleValue)
        default: return nil
        }
    }

    static func traits(_ element: NSAccessibilityElement) -> UInt64? {
        let selector = NSSelectorFromString("accessibilityAttributeValue:")
        guard element.responds(to: selector) else { return nil }
        return (element.perform(selector, with: "AXTraits")?.takeUnretainedValue() as? NSNumber)?.uint64Value
    }

    // MARK: - The translator's delegate

    /// A block that answers one of the translator's requests by asking the device, waiting for the answer.
    @objc(accessibilityTranslationDelegateBridgeCallbackWithToken:)
    func bridgeCallback(token: String) -> AnyObject {
        let block: @convention(block) (AnyObject?) -> AnyObject? = { [weak self] request in
            guard let self, let request else { return Self.emptyResponse() }
            return self.ask(request)
        }
        return block as AnyObject
    }

    private func ask(_ request: AnyObject) -> AnyObject? {
        final class Box: @unchecked Sendable { var response: AnyObject? }
        let box = Box()
        let answered = DispatchSemaphore(value: 0)
        let completion: @convention(block) (AnyObject?) -> Void = { response in
            box.response = response
            answered.signal()
        }
        typealias SendRequest = @convention(c) (AnyObject, Selector, AnyObject, DispatchQueue, AnyObject) -> Void
        let selector = NSSelectorFromString("sendAccessibilityRequestAsync:completionQueue:completionHandler:")
        unsafeBitCast(device.object.method(for: selector), to: SendRequest.self)(device.object, selector, request, replies, completion as AnyObject)
        guard answered.wait(timeout: .now() + Self.requestTimeout) == .success else { return Self.emptyResponse() }
        return box.response ?? Self.emptyResponse()
    }

    @objc(accessibilityTranslationConvertPlatformFrameToSystem:withToken:)
    func convertFrame(_ rect: NSRect, withToken token: String) -> NSRect {
        rect
    }

    @objc(accessibilityTranslationRootParentWithToken:)
    func rootParent(withToken token: String) -> AnyObject? {
        nil
    }

    static func emptyResponse() -> AnyObject? {
        (NSClassFromString("AXPTranslatorResponse") as? NSObject.Type)?.perform(NSSelectorFromString("emptyResponse"))?.takeUnretainedValue()
    }
}

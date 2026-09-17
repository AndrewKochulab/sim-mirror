// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import CoreGraphics
    import Foundation

    /// The app SDK protocol this SDK speaks: protocol/app-sdk/v1 in SimMirror's repository is its one source.
    enum WireProtocol {
        static let version = 1
        static let hierarchyPath = "/v1/hierarchy"
    }

    /// Where something is, in points on the screen held upright, to two decimals.
    struct Frame: Codable, Equatable, Sendable {
        var x: Double
        var y: Double
        var width: Double
        var height: Double

        init(x: Double, y: Double, width: Double, height: Double) {
            self.x = Frame.rounded(x)
            self.y = Frame.rounded(y)
            self.width = Frame.rounded(max(0, width))
            self.height = Frame.rounded(max(0, height))
        }

        init(_ rect: CGRect) {
            let rect = rect.standardized
            self.init(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height)
        }

        var rect: CGRect { CGRect(x: x, y: y, width: width, height: height) }

        static func rounded(_ value: Double) -> Double {
            value.isFinite ? (value * 100).rounded() / 100 : 0
        }
    }

    enum Orientation: String, Codable, Sendable {
        case portrait
        case portraitUpsideDown = "portrait_upside_down"
        case landscapeLeft = "landscape_left"
        case landscapeRight = "landscape_right"
        case unknown
    }

    struct ScreenInfo: Codable, Equatable, Sendable {
        var widthPt: Double
        var heightPt: Double
        var scale: Double
        var orientation: Orientation

        enum CodingKeys: String, CodingKey {
            case widthPt = "width_pt"
            case heightPt = "height_pt"
            case scale, orientation
        }
    }

    struct AppInfo: Codable, Equatable, Sendable {
        var bundleID: String
        var name: String
        var pid: Int32
        var active: Bool

        enum CodingKeys: String, CodingKey {
            case bundleID = "bundle_id"
            case name, pid, active
        }
    }

    enum ModalKind: String, Codable, Sendable {
        case alert, sheet, popover
        case fullScreen = "full_screen"
    }

    struct Modal: Codable, Equatable, Sendable {
        var kind: ModalKind
        var name: String?

        enum CodingKeys: String, CodingKey { case kind, name }

        func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(kind, forKey: .kind)
            try container.encode(name, forKey: .name)
        }
    }

    struct Keyboard: Codable, Equatable, Sendable {
        var frame: Frame
    }

    enum LabelSource: String, Codable, Sendable {
        case accessibility, title, text, descendants, image, identifier, type, tag
    }

    enum Trait: String, Codable, Sendable {
        case selected, editing
    }

    enum NodeSource: String, Codable, Sendable {
        case uikit, swiftui, tag
    }

    extension SimMirror.Kind: Codable {}

    /// A view the app found worth saying.
    struct Node: Codable, Equatable, Sendable {
        var kind: SimMirror.Kind
        var label: String?
        var labelSource: LabelSource?
        var identifier: String?
        var value: String?
        var placeholder: String?
        var frame: Frame
        var traits: [Trait]
        var enabled: Bool
        var interactive: Bool
        var source: NodeSource
        var typeName: String
        var children: [Node]

        enum CodingKeys: String, CodingKey {
            case kind, label, identifier, value, placeholder, frame, traits, enabled, interactive, source, children
            case labelSource = "label_source"
            case typeName = "type_name"
        }

        init(
            kind: SimMirror.Kind,
            label: String? = nil,
            labelSource: LabelSource? = nil,
            identifier: String? = nil,
            value: String? = nil,
            placeholder: String? = nil,
            frame: Frame,
            traits: [Trait] = [],
            enabled: Bool = true,
            interactive: Bool = false,
            source: NodeSource = .uikit,
            typeName: String,
            children: [Node] = []
        ) {
            self.kind = kind
            self.label = label
            self.labelSource = label == nil ? nil : labelSource
            self.identifier = identifier
            self.value = value
            self.placeholder = placeholder
            self.frame = frame
            self.traits = traits
            self.enabled = enabled
            self.interactive = interactive
            self.source = source
            self.typeName = typeName
            self.children = children
        }

        func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(kind, forKey: .kind)
            try container.encode(label, forKey: .label)
            try container.encode(labelSource, forKey: .labelSource)
            try container.encode(identifier, forKey: .identifier)
            try container.encode(value, forKey: .value)
            try container.encode(placeholder, forKey: .placeholder)
            try container.encode(frame, forKey: .frame)
            try container.encode(traits, forKey: .traits)
            try container.encode(enabled, forKey: .enabled)
            try container.encode(interactive, forKey: .interactive)
            try container.encode(source, forKey: .source)
            try container.encode(typeName, forKey: .typeName)
            try container.encode(children, forKey: .children)
        }

        /// How many nodes this one and those inside it are.
        var count: Int { 1 + children.reduce(0) { $0 + $1.count } }
    }

    struct Window: Codable, Equatable, Sendable {
        var level: Double
        var key: Bool
        var nodes: [Node]
    }

    /// What the app answers to GET /v1/hierarchy.
    struct Hierarchy: Codable, Equatable, Sendable {
        var protocolVersion: Int
        var sdkVersion: String
        var app: AppInfo
        var screen: ScreenInfo
        var modal: Modal?
        var keyboard: Keyboard?
        var windows: [Window]
        var truncated: Bool
        var nodeCount: Int
        var captureMs: Double
        var notes: [String]

        enum CodingKeys: String, CodingKey {
            case protocolVersion = "protocol"
            case sdkVersion = "sdk_version"
            case nodeCount = "node_count"
            case captureMs = "capture_ms"
            case app, screen, modal, keyboard, windows, truncated, notes
        }

        func encode(to encoder: Encoder) throws {
            var container = encoder.container(keyedBy: CodingKeys.self)
            try container.encode(protocolVersion, forKey: .protocolVersion)
            try container.encode(sdkVersion, forKey: .sdkVersion)
            try container.encode(app, forKey: .app)
            try container.encode(screen, forKey: .screen)
            try container.encode(modal, forKey: .modal)
            try container.encode(keyboard, forKey: .keyboard)
            try container.encode(windows, forKey: .windows)
            try container.encode(truncated, forKey: .truncated)
            try container.encode(nodeCount, forKey: .nodeCount)
            try container.encode(captureMs, forKey: .captureMs)
            try container.encode(notes, forKey: .notes)
        }
    }

    /// What the app writes to say where it listens.
    struct Listing: Codable, Equatable, Sendable {
        var protocolVersion: Int
        var sdkVersion: String
        var deviceUDID: String
        var bundleID: String
        var name: String
        var pid: Int32
        var port: UInt16
        var secret: String
        var active: Bool
        var startedAt: String

        enum CodingKeys: String, CodingKey {
            case protocolVersion = "protocol"
            case sdkVersion = "sdk_version"
            case deviceUDID = "device_udid"
            case bundleID = "bundle_id"
            case startedAt = "started_at"
            case name, pid, port, secret, active
        }
    }

    enum ErrorCode: String, Codable, Sendable, CaseIterable {
        case badRequest = "bad_request"
        case unauthorized
        case notFound = "not_found"
        case methodNotAllowed = "method_not_allowed"
        case inactive
        case headersTooLarge = "headers_too_large"
        case busy
        case tooLarge = "too_large"

        var status: Int {
            switch self {
            case .badRequest: 400
            case .unauthorized: 401
            case .notFound: 404
            case .methodNotAllowed: 405
            case .inactive: 409
            case .headersTooLarge: 431
            case .busy: 503
            case .tooLarge: 507
            }
        }
    }

    /// Every answer but a hierarchy.
    struct ErrorBody: Codable, Equatable, Sendable {
        struct Detail: Codable, Equatable, Sendable {
            var code: ErrorCode
            var message: String
        }

        var error: Detail

        init(_ code: ErrorCode, _ message: String) {
            error = Detail(code: code, message: message)
        }
    }

    /// Text that says something; empty text is none.
    func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }

    /// The one way the SDK writes JSON: keys sorted, slashes as they are.
    enum WireJSON {
        static func encode<Value: Encodable>(_ value: Value) -> Data {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
            // Every wire type encodes plain strings, numbers and booleans, which JSONEncoder cannot fail on.
            return (try? encoder.encode(value)) ?? Data("{}".utf8)
        }
    }
#endif

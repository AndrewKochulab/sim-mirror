// SPDX-License-Identifier: Apache-2.0
import Foundation

/// A JSON value the helper sends: what an answer is made of when its shape is not fixed, such as an element tree.
public indirect enum JSONValue: Equatable, Sendable, Encodable {
    case null
    case bool(Bool)
    case int(Int)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .null:
            var container = encoder.singleValueContainer()
            try container.encodeNil()
        case .bool(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .int(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .number(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .string(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .array(let values):
            var container = encoder.unkeyedContainer()
            for value in values { try container.encode(value) }
        case .object(let fields):
            var container = encoder.container(keyedBy: Key.self)
            for (key, value) in fields { try container.encode(value, forKey: Key(key)) }
        }
    }

    /// A text that may be missing: JSON null when it is.
    public static func text(_ value: String?) -> JSONValue {
        value.map(JSONValue.string) ?? .null
    }

    struct Key: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init(_ stringValue: String) { self.stringValue = stringValue }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }
}

public enum JSON {
    /// Encodes a value with its keys in a stable order, so the same answer is the same bytes.
    public static func encode<T: Encodable>(_ value: T) -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.nonConformingFloatEncodingStrategy = .convertToString(
            positiveInfinity: "inf", negativeInfinity: "-inf", nan: "nan"
        )
        // Every value the helper sends is a plain struct of numbers, text and JSON values; none can fail to encode.
        return (try? encoder.encode(value)) ?? Data("{}".utf8)
    }
}

/// A request that did not work: why, said so a person can act on it, and the HTTP status it means.
public struct HelperFailure: Error, Equatable, Sendable, Encodable, CustomStringConvertible {
    public let message: String
    public let status: Int

    public init(_ message: String, status: Int = 502) {
        self.message = message
        self.status = status
    }

    public var description: String { message }

    /// What any error is as a failure: itself when it is one, else its description.
    public static func from(_ error: Error) -> HelperFailure {
        if let failure = error as? HelperFailure { return failure }
        return HelperFailure(String(describing: error))
    }
}

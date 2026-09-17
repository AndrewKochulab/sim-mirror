// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Foundation

    /// A request read off a connection: SimMirror only ever sends a GET with no body.
    struct HTTPRequest: Equatable, Sendable {
        var method: String
        var path: String
        var query: [String: String]
        /// Header values by lowercased name; a repeated header keeps its first value.
        var headers: [String: String]

        func header(_ name: String) -> String? { headers[name.lowercased()] }
    }

    enum HTTPParse: Equatable, Sendable {
        /// More bytes are needed.
        case incomplete
        case request(HTTPRequest)
        case failure(ErrorCode, String)
    }

    /// Reads one HTTP/1.1 request head, refusing anything SimMirror would not send.
    enum HTTPRequestParser {
        /// The most a request's head may take.
        static let maxHeadBytes = 8 * 1024
        private static let end = Data("\r\n\r\n".utf8)

        static func parse(_ buffer: Data) -> HTTPParse {
            guard let range = buffer.range(of: end) else {
                return buffer.count >= maxHeadBytes
                    ? .failure(.headersTooLarge, "the request head is too large") : .incomplete
            }
            guard range.lowerBound <= maxHeadBytes else {
                return .failure(.headersTooLarge, "the request head is too large")
            }
            guard let head = String(data: buffer[buffer.startIndex..<range.lowerBound], encoding: .utf8) else {
                return .failure(.badRequest, "the request is not text")
            }
            let lines = head.components(separatedBy: "\r\n")
            let parts = lines[0].split(separator: " ", omittingEmptySubsequences: false)
            guard parts.count == 3, parts[2] == "HTTP/1.1" || parts[2] == "HTTP/1.0", parts[1].hasPrefix("/") else {
                return .failure(.badRequest, "the request line is not HTTP/1.1")
            }
            var headers: [String: String] = [:]
            for line in lines.dropFirst() {
                guard let colon = line.firstIndex(of: ":"), colon != line.startIndex else {
                    return .failure(.badRequest, "a header is malformed")
                }
                let name = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
                if headers[name] == nil { headers[name] = value }
            }
            if headers["transfer-encoding"] != nil || (headers["content-length"].map { $0 != "0" } ?? false) {
                return .failure(.badRequest, "a request has no body")
            }
            let target = parts[1]
            let pathEnd = target.firstIndex(of: "?") ?? target.endIndex
            var query: [String: String] = [:]
            if pathEnd < target.endIndex {
                for pair in target[target.index(after: pathEnd)...].split(separator: "&") {
                    let item = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
                    let key = String(item[0]).removingPercentEncoding ?? String(item[0])
                    let value = item.count > 1 ? (String(item[1]).removingPercentEncoding ?? String(item[1])) : ""
                    if query[key] == nil { query[key] = value }
                }
            }
            return .request(
                HTTPRequest(method: String(parts[0]), path: String(target[..<pathEnd]), query: query, headers: headers)
            )
        }
    }

    /// An answer, always JSON, always closing the connection.
    struct HTTPResponse: Equatable, Sendable {
        var status: Int
        var body: Data

        static func error(_ code: ErrorCode, _ message: String) -> HTTPResponse {
            HTTPResponse(status: code.status, body: WireJSON.encode(ErrorBody(code, message)))
        }

        static let reasons: [Int: String] = [
            200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
            409: "Conflict", 431: "Request Header Fields Too Large", 503: "Service Unavailable",
            507: "Insufficient Storage",
        ]

        var bytes: Data {
            var head = "HTTP/1.1 \(status) \(Self.reasons[status] ?? "Unknown")\r\n"
            head += "Content-Type: application/json\r\n"
            head += "Content-Length: \(body.count)\r\n"
            head += "Cache-Control: no-store\r\n"
            head += "Connection: close\r\n\r\n"
            var data = Data(head.utf8)
            data.append(body)
            return data
        }
    }
#endif

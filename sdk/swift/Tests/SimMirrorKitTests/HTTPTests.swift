// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing

@testable import SimMirrorKit

struct HTTPTests {
    private func parse(_ text: String) -> HTTPParse {
        HTTPRequestParser.parse(Data(text.utf8))
    }

    @Test func aRequestIsReadWithItsPathQueryAndHeaders() {
        let parsed = parse(
            "GET /v1/hierarchy?max_nodes=12&max_nodes=40&flag&name=a%20b HTTP/1.1\r\nHost: 127.0.0.1:1\r\n"
                + "Authorization:  Bearer s \r\nX-Twice: first\r\nx-twice: second\r\n\r\n")
        #expect(
            parsed
                == .request(
                    HTTPRequest(
                        method: "GET", path: "/v1/hierarchy", query: ["max_nodes": "12", "flag": "", "name": "a b"],
                        headers: ["host": "127.0.0.1:1", "authorization": "Bearer s", "x-twice": "first"])))
        if case .request(let request) = parsed {
            #expect(request.header("AUTHORIZATION") == "Bearer s")
        }
        #expect(parse("GET / HTTP/1.0\r\nContent-Length: 0\r\n\r\n") != .incomplete)
        #expect(
            parse("GET /?bad=%zz HTTP/1.1\r\n\r\n")
                == .request(HTTPRequest(method: "GET", path: "/", query: ["bad": "%zz"], headers: [:])))
        #expect(
            parse("GET /?%zz=1 HTTP/1.1\r\n\r\n")
                == .request(HTTPRequest(method: "GET", path: "/", query: ["%zz": "1"], headers: [:])))
    }

    @Test func aHeadNotYetEndedWaitsForMore() {
        #expect(parse("GET /v1/hierarchy HTTP/1.1\r\nHost: x") == .incomplete)
    }

    @Test(arguments: [
        ("GET /v1/hierarchy\r\n\r\n", "the request line is not HTTP/1.1"),
        ("GET v1 HTTP/1.1\r\n\r\n", "the request line is not HTTP/1.1"),
        ("GET / HTTP/2\r\n\r\n", "the request line is not HTTP/1.1"),
        ("GET / HTTP/1.1\r\nno colon\r\n\r\n", "a header is malformed"),
        ("GET / HTTP/1.1\r\n: empty name\r\n\r\n", "a header is malformed"),
        ("GET / HTTP/1.1\r\nContent-Length: 4\r\n\r\nbody", "a request has no body"),
        ("GET / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", "a request has no body"),
    ])
    func whatSimMirrorWouldNotSendIsABadRequest(text: String, message: String) {
        #expect(parse(text) == .failure(.badRequest, message))
    }

    @Test func aHeadThatIsNotTextOrTooLargeIsRefused() {
        var binary = Data([0x47, 0x45, 0x54, 0x20, 0xff, 0xfe])
        binary.append(Data("\r\n\r\n".utf8))
        #expect(HTTPRequestParser.parse(binary) == .failure(.badRequest, "the request is not text"))
        let endless = String(repeating: "a", count: HTTPRequestParser.maxHeadBytes)
        #expect(parse(endless) == .failure(.headersTooLarge, "the request head is too large"))
        let late = "GET / HTTP/1.1\r\nX: " + String(repeating: "a", count: HTTPRequestParser.maxHeadBytes) + "\r\n\r\n"
        #expect(parse(late) == .failure(.headersTooLarge, "the request head is too large"))
    }

    @Test func anAnswerIsJSONThatClosesTheConnection() throws {
        let response = HTTPResponse.error(.inactive, "the app is not in front")
        let text = String(decoding: response.bytes, as: UTF8.self)
        let body = #"{"error":{"code":"inactive","message":"the app is not in front"}}"#
        #expect(
            text
                == "HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: \(body.utf8.count)\r\n"
                + "Cache-Control: no-store\r\nConnection: close\r\n\r\n\(body)")
        #expect(
            String(decoding: HTTPResponse(status: 299, body: Data()).bytes, as: UTF8.self).hasPrefix(
                "HTTP/1.1 299 Unknown\r\n"))
        for code in ErrorCode.allCases {
            #expect(HTTPResponse.reasons[code.status] != nil)
        }
    }
}

struct SecretTests {
    @Test func aSecretIsThirtyTwoRandomBytesInURLSafeBase64() throws {
        let secret = try #require(Secret.make())
        #expect(secret.count == 43)
        #expect(secret.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil)
        #expect(Secret.make() != secret)
        let fixed = Secret.make { bytes in
            bytes = [UInt8](repeating: 0xfb, count: bytes.count)
            return true
        }
        #expect(fixed == String(repeating: "-_v7", count: 10) + "-_s")
        #expect(Secret.make { _ in false } == nil)
    }

    @Test func onlyTheSameSecretMatches() {
        #expect(Secret.matches("abc", "abc"))
        #expect(!Secret.matches("abd", "abc"))
        #expect(!Secret.matches("ab", "abc"))
        #expect(!Secret.matches("abcd", "abc"))
        #expect(!Secret.matches("", "abc"))
    }
}

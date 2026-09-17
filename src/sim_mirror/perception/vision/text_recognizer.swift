// SPDX-License-Identifier: Apache-2.0
//
// SimMirror's text reader: the lines of text in a picture, read with macOS's Vision.
//
// It reads one JSON message a line on stdin and answers each with one on stdout, until stdin ends. What it is asked and
// what it answers are in sim_mirror/perception/vision/helper.py, which runs it; sim_mirror/platform/swift.py compiles
// it on first use with the Xcode a scope uses.

import CoreGraphics
import Foundation
import ImageIO
import Vision

/// The version of what it is asked and answers: helper.py refuses a reader that says hello with another.
let protocolVersion = 1

struct Request: Decodable {
    let id: Int
    let hello: Bool?
    /// A JPEG, in base64.
    let image: String?
    /// "accurate" or "fast".
    let level: String?
    /// Language codes, most likely first; none to have Vision tell.
    let languages: [String]?
    let correction: Bool?
}

struct Identified: Decodable {
    let id: Int
}

/// Where a line is, as shares of the picture from its top left.
struct Box: Encodable {
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

struct Line: Encodable {
    let text: String
    let confidence: Double
    let box: Box
}

struct Answer: Encodable {
    let id: Int
    var version: Int?
    var languages: [String]?
    var lines: [Line]?
    var error: String?
}

@main
struct TextRecognizer {
    static func main() {
        let decoder = JSONDecoder()
        let encoder = JSONEncoder()
        while let message = readLine(strippingNewline: true) {
            guard !message.isEmpty, let data = message.data(using: .utf8) else { continue }
            let reply: Answer? = autoreleasepool {
                if let request = try? decoder.decode(Request.self, from: data) {
                    return answer(to: request)
                }
                if let identified = try? decoder.decode(Identified.self, from: data) {
                    return Answer(id: identified.id, error: "the request could not be read")
                }
                return nil
            }
            guard let reply, let encoded = try? encoder.encode(reply) else { continue }
            FileHandle.standardOutput.write(encoded + Data("\n".utf8))
        }
    }

    static func answer(to request: Request) -> Answer {
        if request.hello == true {
            return Answer(id: request.id, version: protocolVersion, languages: supportedLanguages())
        }
        guard
            let encoded = request.image,
            let data = Data(base64Encoded: encoded),
            let source = CGImageSourceCreateWithData(data as CFData, nil),
            let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            return Answer(id: request.id, error: "the picture could not be decoded")
        }
        let reading = VNRecognizeTextRequest()
        reading.recognitionLevel = request.level == "fast" ? .fast : .accurate
        reading.usesLanguageCorrection = request.correction ?? true
        if let languages = request.languages, !languages.isEmpty {
            reading.recognitionLanguages = languages
        } else if #available(macOS 13.0, *) {
            reading.automaticallyDetectsLanguage = true
        }
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([reading])
        } catch {
            return Answer(id: request.id, error: error.localizedDescription)
        }
        let lines = (reading.results ?? []).compactMap { observation -> Line? in
            guard let best = observation.topCandidates(1).first else { return nil }
            let found = observation.boundingBox
            let box = Box(x: found.minX, y: 1 - found.maxY, w: found.width, h: found.height)
            return Line(text: best.string, confidence: Double(best.confidence), box: box)
        }
        return Answer(id: request.id, lines: lines)
    }

    static func supportedLanguages() -> [String] {
        (try? VNRecognizeTextRequest().supportedRecognitionLanguages()) ?? []
    }
}

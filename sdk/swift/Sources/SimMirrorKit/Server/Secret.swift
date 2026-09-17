// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Foundation
    import Security

    /// What a request must carry to be answered: 32 random bytes, new each launch, in URL-safe base64.
    enum Secret {
        static let byteCount = 32

        /// Fills a buffer with random bytes, answering whether it could.
        typealias Random = @Sendable (inout [UInt8]) -> Bool

        static let systemRandom: Random = { bytes in
            SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess
        }

        /// A new secret, or nil when the system has no randomness to give.
        static func make(random: Random = systemRandom) -> String? {
            var bytes = [UInt8](repeating: 0, count: byteCount)
            guard random(&bytes) else { return nil }
            return Data(bytes).base64EncodedString()
                .replacingOccurrences(of: "+", with: "-")
                .replacingOccurrences(of: "/", with: "_")
                .replacingOccurrences(of: "=", with: "")
        }

        /// Whether `given` is `expected`, taking as long whatever it is, so its time says nothing of how close it is.
        static func matches(_ given: String, _ expected: String) -> Bool {
            let left = Array(given.utf8)
            let right = Array(expected.utf8)
            var difference = UInt8(truncatingIfNeeded: left.count ^ right.count)
            for index in 0..<right.count {
                difference |= (index < left.count ? left[index] : 0) ^ right[index]
            }
            return difference == 0 && left.count == right.count
        }
    }
#endif

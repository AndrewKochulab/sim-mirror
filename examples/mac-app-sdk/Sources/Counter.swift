// SPDX-License-Identifier: Apache-2.0
/// Shared by both sample windows so their reset action has the same behavior.
struct Counter {
    private(set) var value = 0
    mutating func increment() { value += 1 }
    mutating func reset() { value = 0 }
}

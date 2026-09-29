// SPDX-License-Identifier: Apache-2.0
import Foundation

/// Work a device does once, in the background, before its first request -- and a wait for it to be done.
final class Warming: @unchecked Sendable {
    private let group = DispatchGroup()
    private let lock = NSLock()
    private var started = false

    /// Start `body` in the background, unless it was started before.
    func start(_ body: @escaping @Sendable () -> Void) {
        lock.lock()
        defer { lock.unlock() }
        guard !started else { return }
        started = true
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async { [group] in
            body()
            group.leave()
        }
    }

    /// Returns once the work is done, or at once when none was started.
    func done() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            group.notify(queue: .global()) { continuation.resume() }
        }
    }
}

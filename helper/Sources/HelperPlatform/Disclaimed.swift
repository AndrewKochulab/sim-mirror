// SPDX-License-Identifier: Apache-2.0
import Darwin
import Foundation
import HelperCore

/// Starting this program again as its own responsible process, so macOS asks it -- not the terminal or agent that
/// started SimMirror -- for the Camera (`Disclaim`).
///
/// `responsibility_spawnattrs_setdisclaim` is how Xcode and other developer tools start a program that asks for
/// permissions of its own; it is looked up at run time, and a Mac without it runs the program as it is.
public enum Disclaimed {
    private typealias SetDisclaim = @convention(c) (UnsafeMutablePointer<posix_spawnattr_t?>, Int32) -> Int32

    /// Run this program again with the same arguments, disclaimed, and answer its exit status; nil when it cannot be.
    /// A signal this process is sent is passed on to it.
    public static func run() -> Int32? {
        guard let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "responsibility_spawnattrs_setdisclaim") else {
            return nil
        }
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        guard unsafeBitCast(symbol, to: SetDisclaim.self)(&attributes, 1) == 0 else { return nil }
        var environment = ProcessInfo.processInfo.environment
        environment[Disclaim.variable] = "1"
        let program = Bundle.main.executablePath ?? ProcessInfo.processInfo.arguments[0]
        var argv = ProcessInfo.processInfo.arguments.map { strdup($0) } + [nil]
        var envp = environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            (argv + envp).forEach { free($0) }
        }
        var child: pid_t = 0
        guard posix_spawn(&child, program, nil, &attributes, &argv, &envp) == 0 else { return nil }
        for code in [SIGTERM, SIGINT, SIGHUP] {
            signal(code, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: code, queue: .global())
            source.setEventHandler { kill(child, code) }
            source.resume()
            _ = Unmanaged.passRetained(source)
        }
        var status: Int32 = 0
        while waitpid(child, &status, 0) == -1, errno == EINTR {}
        let signalled = status & 0x7f
        return signalled == 0 ? (status >> 8) & 0xff : 128 + signalled
    }
}

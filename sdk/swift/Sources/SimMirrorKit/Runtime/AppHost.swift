// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Foundation
    import UIKit
    import os

    /// Where the app runs: which simulator, which app, and whether it is somewhere the SDK should run at all.
    struct ProcessContext: Sendable {
        var environment: [String: String]
        var bundleID: String?
        var name: String
        var pid: Int32
        var home: String
        var bundlePath: String
        var os: OperatingSystemVersion

        static func current(bundle: Bundle = .main) -> ProcessContext {
            ProcessContext(
                environment: ProcessInfo.processInfo.environment,
                bundleID: bundle.bundleIdentifier,
                name: name(info: bundle.infoDictionary, process: ProcessInfo.processInfo.processName),
                pid: getpid(),
                home: NSHomeDirectory(),
                bundlePath: bundle.bundlePath,
                os: ProcessInfo.processInfo.operatingSystemVersion
            )
        }

        /// The app's name as the home screen shows it, else its bundle's name, else its process's.
        static func name(info: [String: Any]?, process: String) -> String {
            info?["CFBundleDisplayName"] as? String ?? info?["CFBundleName"] as? String ?? process
        }

        var deviceUDID: String? { environment["SIMULATOR_UDID"] }
        var sharedFolder: String? { environment["SIMULATOR_SHARED_RESOURCES_DIRECTORY"] }

        /// Why the SDK does not run here, or nil when it does.
        var refusal: String? {
            if environment["XCODE_RUNNING_FOR_PREVIEWS"] == "1" { return "not in an Xcode preview" }
            if bundlePath.hasSuffix(".appex") { return "not in an app extension" }
            guard deviceUDID != nil else { return "SIMULATOR_UDID is not set, so this is not a simulator" }
            guard let bundleID, bundleID.range(of: #"^[A-Za-z0-9.-]{1,155}$"#, options: .regularExpression) != nil
            else { return "the app has no bundle identifier SimMirror can name its listing after" }
            return nil
        }
    }

    /// What happens to the app that changes what SimMirror is told.
    enum AppEvent: Equatable, Sendable {
        case active
        case inactive
        case terminating
    }

    /// The running app, as the SDK needs it: whether it is in front, its windows, and where the keyboard is.
    @MainActor
    protocol AppHost: AnyObject {
        var isActive: Bool { get }
        var windows: [UIWindow] { get }
        /// The keyboard's frame on the screen held upright, while it shows.
        var keyboardFrame: CGRect? { get }
        func observe(_ handler: @escaping @MainActor (AppEvent) -> Void)
        func stopObserving()
    }

    /// The app as UIKit knows it.
    @MainActor
    final class UIKitAppHost: AppHost {
        private let center: NotificationCenter
        private let application: () -> UIApplication?
        private var observers: [NSObjectProtocol] = []
        private(set) var keyboardFrame: CGRect?

        init(center: NotificationCenter = .default, application: @escaping () -> UIApplication? = UIKitAppHost.shared) {
            self.center = center
            self.application = application
        }

        /// The shared application, looked up without naming `UIApplication.shared`, which app extensions cannot use.
        nonisolated static func shared() -> UIApplication? {
            UIApplication.value(forKey: "sharedApplication") as? UIApplication
        }

        var isActive: Bool { application()?.applicationState == .active }

        var windows: [UIWindow] {
            let scenes = application()?.connectedScenes ?? []
            return scenes.compactMap { $0 as? UIWindowScene }
                .filter { [.foregroundActive, .foregroundInactive].contains($0.activationState) }
                .flatMap(\.windows)
        }

        func observe(_ handler: @escaping @MainActor (AppEvent) -> Void) {
            stopObserving()
            let events: [(Notification.Name, AppEvent)] = [
                (UIApplication.didBecomeActiveNotification, .active),
                (UIApplication.didEnterBackgroundNotification, .inactive),
                (UIApplication.willTerminateNotification, .terminating),
            ]
            for (name, event) in events {
                observers.append(
                    center.addObserver(forName: name, object: nil, queue: .main) { _ in
                        MainActor.assumeIsolated { handler(event) }
                    })
            }
            observers.append(
                center.addObserver(forName: UIResponder.keyboardWillChangeFrameNotification, object: nil, queue: .main)
                {
                    note in
                    let frame = note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect
                    MainActor.assumeIsolated { self.keyboardChanged(to: frame) }
                })
            observers.append(
                center.addObserver(forName: UIResponder.keyboardWillHideNotification, object: nil, queue: .main) { _ in
                    MainActor.assumeIsolated { self.keyboardChanged(to: nil) }
                })
        }

        func stopObserving() {
            observers.forEach(center.removeObserver)
            observers = []
            keyboardFrame = nil
        }

        /// The keyboard's end frame, given in the screen's interface coordinates, held upright; none when it is off
        /// screen.
        func keyboardChanged(to frame: CGRect?) {
            let screen = windows.first?.windowScene?.screen ?? UIScreen.main
            guard let frame, frame.height > 0, frame.intersects(screen.bounds) else {
                keyboardFrame = nil
                return
            }
            keyboardFrame = screen.coordinateSpace.convert(frame, to: screen.fixedCoordinateSpace)
        }
    }

    /// Where the SDK says what it does, never with the secret.
    struct Log: Sendable {
        var write: @Sendable (String) -> Void

        static let system = Log { message in
            Logger(subsystem: "io.github.andrewkochulab.simmirror", category: "SimMirrorKit").notice(
                "\(message, privacy: .public)")
        }
    }
#endif

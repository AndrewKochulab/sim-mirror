// SPDX-License-Identifier: Apache-2.0
import Foundation
import Testing
import UIKit

@testable import AppSDK
@testable import SimMirrorKit

/// The SDK inside a running app: the app started it, so a listing is written and SimMirror's request is answered with
/// what the app shows. These are the parts a unit test cannot reach -- a real application, its scenes and a real
/// presentation.
@MainActor
@Suite(.serialized)
struct AppSDKTests {
    private func listing() throws -> Listing {
        let folder = try #require(ProcessInfo.processInfo.environment["SIMULATOR_SHARED_RESOURCES_DIRECTORY"])
        let bundleID = try #require(Bundle.main.bundleIdentifier)
        let path = "\(folder)/\(DiscoveryWriter.relativeFolder)/\(bundleID).json"
        return try JSONDecoder().decode(Listing.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    }

    private func ask(_ listing: Listing, secret: String?, origin: String? = nil) async throws -> (Int, Data) {
        var request = URLRequest(url: try #require(URL(string: "http://127.0.0.1:\(listing.port)/v1/hierarchy")))
        request.setValue(secret.map { "Bearer \($0)" }, forHTTPHeaderField: "Authorization")
        request.setValue(origin, forHTTPHeaderField: "Origin")
        let (data, response) = try await URLSession.shared.data(for: request)
        return (try #require(response as? HTTPURLResponse).statusCode, data)
    }

    private func all(_ nodes: [Node]) -> [Node] {
        nodes.flatMap { [$0] + all($0.children) }
    }

    private func waitUntilActive() async throws {
        for _ in 0..<100 where UIApplication.shared.applicationState != .active {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    @Test func theAppAnswersSimMirrorWithWhatItShows() async throws {
        try await waitUntilActive()
        #expect(SimMirror.isRunning)
        let listing = try listing()
        #expect(listing.bundleID == Bundle.main.bundleIdentifier && listing.pid == getpid())
        let (status, data) = try await ask(listing, secret: listing.secret)
        #expect(status == 200)
        let hierarchy = try JSONDecoder().decode(Hierarchy.self, from: data)
        #expect(hierarchy.app.active && hierarchy.screen.orientation != .unknown)
        let labels = all(hierarchy.windows.flatMap(\.nodes)).compactMap(\.label)
        #expect(labels.contains("SwiftUI"))
        #expect(try await ask(listing, secret: nil).0 == 401)
        #expect(try await ask(listing, secret: listing.secret, origin: "https://example.com").0 == 400)
    }

    @Test func theHostSeesTheRunningAppItsScenesAndItsWindows() throws {
        let host = UIKitAppHost()
        #expect(host.isActive)
        #expect(!host.windows.isEmpty)
        #expect(UIKitAppHost.shared() === UIApplication.shared)
    }

    @Test func aPresentedAlertIsTheModalAndWhatItHidesIsLeftOut() async throws {
        try await waitUntilActive()
        let host = UIKitAppHost()
        let window = try #require(host.windows.first { $0.isKeyWindow })
        let root = try #require(window.rootViewController)
        let alert = UIAlertController(title: "Delete the list?", message: nil, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Delete", style: .destructive))
        await withCheckedContinuation { done in root.present(alert, animated: false) { done.resume() } }
        let capturer = HierarchyCapturer(
            app: AppInfo(bundleID: "tests", name: "Tests", pid: getpid(), active: true), describers: DescriberRegistry()
        )
        let hierarchy = capturer.capture(windows: host.windows, keyboard: nil, maxNodes: 500)
        #expect(hierarchy.modal == Modal(kind: .alert, name: "Delete the list?"))
        let labels = all(hierarchy.windows.flatMap(\.nodes)).compactMap(\.label)
        #expect(labels.contains("Delete") && !labels.contains("Daily mix"))
        await withCheckedContinuation { done in alert.dismiss(animated: false) { done.resume() } }
    }

    @Test func theRatingControlIsDescribedByTheAppsOwnDescriber() {
        let rating = RatingControl()
        let described = Runtime.shared.describers.describe(rating)
        #expect(described.kind == .slider && described.label == "Rating" && described.value == "3 of 5")
        #expect(RatingControlDescriber().describe(UIView()) == nil)
    }
}

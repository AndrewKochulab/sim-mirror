// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import Darwin
    import Foundation

    /// The file operations the listing needs, so each way they fail can be tested.
    struct FileSystem: Sendable {
        /// Makes a folder and those above it that are missing, each readable by its owner only.
        var makeFolder: @Sendable (String) -> Bool
        /// Writes a file readable by its owner only, whole or not at all.
        var writeAtomically: @Sendable (Data, String) -> Bool
        var read: @Sendable (String) -> Data?
        var remove: @Sendable (String) -> Void
    }

    extension FileSystem {
        static let live = FileSystem(
            makeFolder: { path in
                var built = ""
                for part in path.split(separator: "/") {
                    built += "/\(part)"
                    if mkdir(built, 0o700) != 0 && errno != EEXIST { return false }
                }
                var status = stat()
                return lstat(path, &status) == 0 && (status.st_mode & S_IFMT) == S_IFDIR
            },
            writeAtomically: { data, path in
                let temporary = "\(path).\(getpid()).tmp"
                let descriptor = open(temporary, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW, 0o600)
                guard descriptor >= 0 else { return false }
                let wrote = data.withUnsafeBytes { raw in
                    raw.baseAddress.map { Darwin.write(descriptor, $0, raw.count) } ?? 0
                }
                let synced = fsync(descriptor) == 0
                close(descriptor)
                guard wrote == data.count, synced, rename(temporary, path) == 0 else {
                    unlink(temporary)
                    return false
                }
                return true
            },
            read: { path in FileManager.default.contents(atPath: path) },
            remove: { path in _ = unlink(path) }
        )
    }

    /// Writes the listing that tells SimMirror where the app listens, and takes it back.
    ///
    /// It goes in the simulator's shared data folder, where SimMirror looks first, or else in the app's own caches.
    final class DiscoveryWriter: @unchecked Sendable {
        static let relativeFolder = "Library/Caches/SimMirror/apps"

        private let folders: [String]
        private let fileSystem: FileSystem
        private(set) var path: String?

        /// `sharedFolder` is the simulator's data folder, `home` the app's own.
        init(sharedFolder: String?, home: String, fileSystem: FileSystem = .live) {
            self.folders = [sharedFolder, home].compactMap { $0 }.map { "\($0)/\(Self.relativeFolder)" }
            self.fileSystem = fileSystem
        }

        /// Writes the listing where it can, answering where; nil when nowhere could take it.
        @discardableResult
        func write(_ listing: Listing) -> String? {
            let data = WireJSON.encode(listing)
            let candidates = path.map { [$0] } ?? folders.map { "\($0)/\(listing.bundleID).json" }
            for candidate in candidates {
                let folder = (candidate as NSString).deletingLastPathComponent
                if fileSystem.makeFolder(folder), fileSystem.writeAtomically(data, candidate) {
                    path = candidate
                    return candidate
                }
            }
            return nil
        }

        /// Removes the listing, but only while it is still this launch's: a newer launch may have replaced it.
        func remove(_ listing: Listing) {
            guard let path else { return }
            self.path = nil
            guard let data = fileSystem.read(path),
                let written = try? JSONDecoder().decode(Listing.self, from: data),
                written.pid == listing.pid, written.startedAt == listing.startedAt
            else { return }
            fileSystem.remove(path)
        }
    }
#endif

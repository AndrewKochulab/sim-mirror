// SPDX-License-Identifier: Apache-2.0
import SimMirrorKit
import SwiftUI

/// The same views, named for SimMirror with `.simMirror(...)`: a Debug build on a simulator reads the names, and
/// anything else ignores them.
struct TaggedScreen: View {
    @State private var notifications = false
    @State private var taps = 0

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    HStack(spacing: 28) {
                        Button {
                            taps += 1
                        } label: {
                            Image(systemName: "gearshape")
                        }
                        .simMirror("Settings")
                        Button {
                        } label: {
                            Image(systemName: "trash")
                        }
                        .simMirror(kind: .button, name: "Delete list", identifier: "delete-list")
                    }
                    .font(.title2)
                    VStack(spacing: 6) {
                        Image(systemName: "sun.max").font(.largeTitle)
                        Text("Daily mix")
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.mint.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
                    .onTapGesture { taps += 1 }
                    .simMirror(kind: .button, name: "Play daily mix")
                    Toggle("", isOn: $notifications).labelsHidden()
                        .simMirror(kind: .switch, name: "Notifications")
                }
                .padding()
            }
            .navigationTitle("Tagged")
        }
    }
}

// SPDX-License-Identifier: Apache-2.0
import SwiftUI

/// SwiftUI as it is often written: nothing here says what it is for accessibility.
struct SwiftUIScreen: View {
    @State private var notifications = false
    @State private var title = ""
    @State private var password = ""
    @State private var taps = 0
    @State private var showsSheet = false
    @State private var showsAlert = false

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
                        Button {
                            showsAlert = true
                        } label: {
                            Image(systemName: "trash")
                        }
                    }
                    .font(.title2)
                    VStack(spacing: 6) {
                        Image(systemName: "sun.max").font(.largeTitle)
                        Text("Daily mix")
                        Text("Played \(taps) times").font(.footnote).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.yellow.opacity(0.25), in: RoundedRectangle(cornerRadius: 12))
                    .onTapGesture { taps += 1 }
                    Toggle("", isOn: $notifications).labelsHidden()
                    TextField("Title", text: $title).textFieldStyle(.roundedBorder)
                    SecureField("Password", text: $password).textFieldStyle(.roundedBorder)
                    Button("Show sheet") { showsSheet = true }
                }
                .padding()
            }
            .navigationTitle("SwiftUI")
            .sheet(isPresented: $showsSheet) {
                VStack(spacing: 16) {
                    Text("Filters").font(.headline)
                    Button {
                        showsSheet = false
                    } label: {
                        Image(systemName: "xmark.circle")
                    }
                }
                .presentationDetents([.medium])
            }
            .alert("Delete the list?", isPresented: $showsAlert) {
                Button("Delete", role: .destructive) {}
                Button("Cancel", role: .cancel) {}
            }
        }
    }
}

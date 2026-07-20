import SwiftUI

struct SettingsView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var showingSignIn = false

    var body: some View {
        @Bindable var state = state

        NavigationStack {
            Form {
                Section("Connection") {
                    TextField("API base URL", text: $state.baseURLText)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()

                    HStack {
                        Label(state.isSignedIn ? "Signed in" : "Signed out", systemImage: state.isSignedIn ? "checkmark.circle.fill" : "person.crop.circle")
                        Spacer()
                        Text(state.userName ?? "No account")
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Button {
                        if state.isSignedIn {
                            state.signOut()
                        } else {
                            showingSignIn = true
                        }
                    } label: {
                        Label(state.isSignedIn ? "Sign out" : "Sign in", systemImage: state.isSignedIn ? "rectangle.portrait.and.arrow.right" : "person.crop.circle.badge.checkmark")
                    }

                    Button {
                        Task { await state.refresh() }
                    } label: {
                        Label("Refresh data", systemImage: "arrow.clockwise")
                    }
                }

                Section("Owner notifications") {
                    HStack {
                        Label("Device alerts", systemImage: state.notificationsEnabled ? "bell.badge.fill" : "bell.slash.fill")
                        Spacer()
                        Text(state.notificationsEnabled ? "On" : "Off")
                            .foregroundStyle(.secondary)
                    }

                    Button {
                        Task { await state.enableOwnerNotifications() }
                    } label: {
                        Label("Enable alerts", systemImage: "checkmark.circle.fill")
                    }
                    .disabled(state.notificationsEnabled)
                }

                Section("Business") {
                    InfoLine(title: "Monthly target", value: Currency.kes(AppState.monthlyRevenueTarget))
                    InfoLine(title: "Currency", value: "Kenyan Shilling")
                    InfoLine(title: "Location", value: "Star Shopping Mall")
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(PWTheme.background)
        }
        .sheet(isPresented: $showingSignIn) {
            SignInView()
                .presentationDetents([.medium, .large])
        }
    }
}

private struct InfoLine: View {
    let title: String
    let value: String

    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
        }
    }
}

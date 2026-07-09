import SwiftUI

struct SignInView: View {
    @Environment(AppState.self) private var state
    @Environment(\.dismiss) private var dismiss
    @State private var username = ""
    @State private var password = ""
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Connect to Party World")
                        .font(.title2.weight(.bold))
                    Text("Use your Odoo or reports credentials to load live sales, stock, and customer data.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                VStack(spacing: 12) {
                    TextField("Username", text: $username)
                        .textContentType(.username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textFieldStyle(.roundedBorder)

                    SecureField("Password", text: $password)
                        .textContentType(.password)
                        .textFieldStyle(.roundedBorder)
                }

                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(PWTheme.coral)
                }

                Button {
                    Task { await signIn() }
                } label: {
                    HStack {
                        if state.isLoading {
                            ProgressView()
                                .tint(.white)
                        }
                        Text("Sign in")
                            .fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(username.isEmpty || password.isEmpty || state.isLoading)

                Spacer()
            }
            .padding(22)
            .background(PWTheme.background.ignoresSafeArea())
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }

    private func signIn() async {
        do {
            try await state.signIn(username: username, password: password)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

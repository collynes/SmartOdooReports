import SwiftUI

enum PWTheme {
    static let background = Color(red: 0.968, green: 0.973, blue: 0.960)
    static let surface = Color.white
    static let ink = Color(red: 0.118, green: 0.145, blue: 0.165)
    static let secondaryInk = Color(red: 0.384, green: 0.435, blue: 0.463)
    static let sky = Color(red: 0.322, green: 0.655, blue: 0.918)
    static let coral = Color(red: 0.933, green: 0.404, blue: 0.357)
    static let honey = Color(red: 0.961, green: 0.698, blue: 0.267)
    static let mint = Color(red: 0.298, green: 0.714, blue: 0.565)
    static let lavender = Color(red: 0.600, green: 0.529, blue: 0.855)
    static let softLine = Color.black.opacity(0.07)

    static func tint(for tone: InsightNote.Tone) -> Color {
        switch tone {
        case .helpful:
            sky
        case .attention:
            honey
        case .positive:
            mint
        }
    }
}

struct SoftCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .background(PWTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(PWTheme.softLine, lineWidth: 1)
            }
            .shadow(color: Color.black.opacity(0.04), radius: 12, x: 0, y: 6)
    }
}

struct SectionHeader: View {
    let title: String
    var actionTitle: String?
    var action: (() -> Void)?

    var body: some View {
        HStack {
            Text(title)
                .font(.headline)
                .foregroundStyle(PWTheme.ink)
            Spacer()
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .font(.subheadline.weight(.semibold))
            }
        }
        .padding(.horizontal, 4)
    }
}

struct EmptyStateView: View {
    let symbol: String
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 28, weight: .semibold))
                .foregroundStyle(PWTheme.mint)
            Text(title)
                .font(.headline)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(28)
        .background(PWTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

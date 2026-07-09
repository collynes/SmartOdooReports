import SwiftUI

struct AlertsView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    summaryCard

                    if state.ownerAlerts.isEmpty {
                        EmptyStateView(
                            symbol: state.hasLiveData ? "checkmark.seal.fill" : "wifi.slash",
                            title: state.hasLiveData ? "Nothing needs attention" : "Waiting for live data",
                            message: state.hasLiveData ? "Stock, sales pace, cash flow, and expenses look calm." : "Owner alerts will load after sign-in."
                        )
                    } else {
                        ForEach(state.ownerAlerts) { alert in
                            OwnerAlertRow(alert: alert)
                        }
                    }
                }
                .padding(18)
            }
            .background(PWTheme.background.ignoresSafeArea())
            .navigationTitle("Owner Alerts")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await state.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("Refresh")
                }
            }
        }
    }

    private var summaryCard: some View {
        let critical = state.ownerAlerts.filter { $0.priority == .critical }.count
        let warning = state.ownerAlerts.filter { $0.priority == .warning }.count

        return SoftCard {
            HStack(spacing: 14) {
                Image(systemName: critical > 0 ? "exclamationmark.triangle.fill" : "bell.fill")
                    .font(.title3)
                    .foregroundStyle(critical > 0 ? PWTheme.coral : PWTheme.sky)
                    .frame(width: 42, height: 42)
                    .background((critical > 0 ? PWTheme.coral : PWTheme.sky).opacity(0.14))
                    .clipShape(Circle())

                VStack(alignment: .leading, spacing: 4) {
                    Text(summaryTitle(critical: critical, warning: warning))
                        .font(.headline)
                        .foregroundStyle(PWTheme.ink)
                    Text(state.hasLiveData ? "Updated from live Party World data." : "No alerts are shown until live data loads.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Spacer()
            }
        }
    }

    private func summaryTitle(critical: Int, warning: Int) -> String {
        if critical > 0 {
            return "\(critical) urgent item\(critical == 1 ? "" : "s")"
        }
        if warning > 0 {
            return "\(warning) item\(warning == 1 ? "" : "s") to watch"
        }
        return state.hasLiveData ? "Everything looks calm" : "No live alerts"
    }
}

private struct OwnerAlertRow: View {
    let alert: OwnerAlert

    var body: some View {
        SoftCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: symbol)
                        .font(.headline)
                        .foregroundStyle(tint)
                        .frame(width: 36, height: 36)
                        .background(tint.opacity(0.14))
                        .clipShape(Circle())

                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 8) {
                            Text(alert.category.capitalized)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(tint)
                            Text(alert.priority.rawValue.capitalized)
                                .font(.caption2.weight(.bold))
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .background(tint.opacity(0.13))
                                .foregroundStyle(tint)
                                .clipShape(Capsule())
                        }

                        Text(alert.title)
                            .font(.headline)
                            .foregroundStyle(PWTheme.ink)
                            .fixedSize(horizontal: false, vertical: true)

                        Text(alert.body)
                            .font(.subheadline)
                            .foregroundStyle(PWTheme.secondaryInk)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                HStack {
                    if let metricLabel = alert.metricLabel, let metricValue = alert.metricValue {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(metricLabel)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Text(metricText(metricValue))
                                .font(.subheadline.weight(.bold))
                                .foregroundStyle(PWTheme.ink)
                        }
                    }

                    Spacer()

                    if let actionLabel = alert.actionLabel {
                        Label(actionLabel, systemImage: "arrow.right.circle.fill")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(tint)
                    }
                }
            }
        }
    }

    private var tint: Color {
        switch alert.priority {
        case .critical:
            PWTheme.coral
        case .warning:
            PWTheme.honey
        case .info:
            PWTheme.sky
        }
    }

    private var symbol: String {
        switch alert.category {
        case "stock":
            "shippingbox.fill"
        case "sales", "target":
            "target"
        case "cashflow":
            "banknote.fill"
        case "expenses":
            "creditcard.fill"
        default:
            "bell.fill"
        }
    }

    private func metricText(_ value: Double) -> String {
        if alert.category == "stock" {
            return value.formatted(.number.precision(.fractionLength(0)))
        }
        return Currency.kes(value)
    }
}

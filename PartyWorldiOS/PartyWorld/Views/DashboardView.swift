import SwiftUI

struct DashboardView: View {
    @Environment(AppState.self) private var state
    @Binding var showingSignIn: Bool
    @Binding var showingSettings: Bool

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12)
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    hero
                    connectionBanner
                    if state.hasLiveData {
                        kpis
                        targetCard
                        topProducts
                    }
                }
                .padding(18)
            }
            .background(PWTheme.background.ignoresSafeArea())
            .refreshable { await state.refresh() }
            .navigationTitle("Party World")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showingSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                    }
                    .accessibilityLabel("Settings")
                }

                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await state.refresh() }
                    } label: {
                        Image(systemName: state.isLoading ? "arrow.triangle.2.circlepath" : "arrow.clockwise")
                    }
                    .disabled(state.isLoading)
                    .accessibilityLabel("Refresh")
                }
            }
        }
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Good day\(state.userName.map { ", \($0)" } ?? "")")
                .font(.title2.weight(.bold))
                .foregroundStyle(PWTheme.ink)
            Text("Your current shop snapshot.")
                .font(.subheadline)
                .foregroundStyle(PWTheme.secondaryInk)
            HStack(spacing: 10) {
                Label(connectionLabel, systemImage: connectionSymbol)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(connectionTint.opacity(0.16))
                    .foregroundStyle(connectionTint)
                    .clipShape(Capsule())

                if let lastUpdated = state.lastUpdated {
                    Text(lastUpdated.formatted(date: .omitted, time: .shortened))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var connectionBanner: some View {
        if state.hasLiveData == false || state.isDataStale {
            SoftCard {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top, spacing: 12) {
                        IconBadge(symbol: state.isDataStale ? "exclamationmark.arrow.triangle.2.circlepath" : "lock.fill", tint: state.isDataStale ? PWTheme.honey : PWTheme.sky, size: 34)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(state.isDataStale ? "Showing an older snapshot" : "Sign in to load live data")
                                .font(.subheadline.weight(.semibold))
                            Text(state.notice ?? "No business data is shown until the server responds.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    Button {
                        if state.isSignedIn {
                            Task { await state.refresh() }
                        } else {
                            showingSignIn = true
                        }
                    } label: {
                        Text(state.isSignedIn ? "Retry" : "Sign in")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
            }
        }
    }

    private var kpis: some View {
        LazyVGrid(columns: columns, spacing: 12) {
            MetricCard(title: "Today", value: Currency.kes(state.dashboard.revenueToday), symbol: "banknote.fill", tint: PWTheme.mint)
            MetricCard(title: "This month", value: Currency.kes(state.dashboard.revenueMonth), symbol: "calendar", tint: PWTheme.sky)
            MetricCard(title: "Orders", value: "\(state.dashboard.ordersToday)", symbol: "bag.fill", tint: PWTheme.coral)
            MetricCard(title: "Stock value", value: Currency.kes(state.dashboard.stockValue), symbol: "shippingbox.fill", tint: PWTheme.lavender)
        }
    }

    private var targetCard: some View {
        SoftCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Label("Monthly target", systemImage: "target")
                        .font(.headline)
                    Spacer()
                    Text("\(Int(state.monthlyTargetProgress * 100))%")
                        .font(.headline.weight(.bold))
                        .foregroundStyle(PWTheme.coral)
                }

                ProgressView(value: state.monthlyTargetProgress)
                    .tint(PWTheme.coral)
                    .scaleEffect(x: 1, y: 1.4, anchor: .center)

                Text("\(Currency.kes(state.dashboard.revenueMonth)) of \(Currency.kes(state.monthlyRevenueTarget))")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var connectionLabel: String {
        if state.isDataStale { return "Update failed" }
        return state.hasLiveData ? "Live data" : "Not connected"
    }

    private var connectionSymbol: String {
        if state.isDataStale { return "exclamationmark.triangle.fill" }
        return state.hasLiveData ? "checkmark.circle.fill" : "wifi.slash"
    }

    private var connectionTint: Color {
        state.isDataStale ? PWTheme.honey : (state.hasLiveData ? PWTheme.mint : PWTheme.honey)
    }

    private var insights: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(title: "Worth noticing")
            if state.insightNotes.isEmpty {
                EmptyStateView(
                    symbol: state.hasLiveData ? "checkmark.seal.fill" : "wifi.slash",
                    title: state.hasLiveData ? "Nothing unusual yet" : "Waiting for live data",
                    message: state.hasLiveData ? "Owner notes will appear when something needs attention." : "Sign in or refresh after the API is available."
                )
            } else {
                ForEach(state.insightNotes) { note in
                    InsightRow(note: note)
                }
            }
        }
    }

    private var topProducts: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionHeader(title: "Top products")
            if state.dashboard.topProductsMonth.isEmpty {
                EmptyStateView(
                    symbol: "chart.bar.xaxis",
                    title: state.hasLiveData ? "No top products yet" : "Waiting for live data",
                    message: state.hasLiveData ? "Top sellers will appear after confirmed sales are available." : "Live product performance will load after sign-in."
                )
            } else {
                SoftCard {
                    VStack(spacing: 14) {
                        ForEach(state.dashboard.topProductsMonth) { product in
                            ProductBar(product: product, maxRevenue: state.dashboard.topProductsMonth.map(\.revenue).max() ?? 1)
                        }
                    }
                }
            }
        }
    }
}

private struct MetricCard: View {
    let title: String
    let value: String
    let symbol: String
    let tint: Color

    var body: some View {
        SoftCard {
            VStack(alignment: .leading, spacing: 12) {
                IconBadge(symbol: symbol, tint: tint, size: 34)
                Text(value)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(PWTheme.ink)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct InsightRow: View {
    let note: InsightNote

    var body: some View {
        let tint = PWTheme.tint(for: note.tone)
        SoftCard {
            HStack(alignment: .top, spacing: 12) {
                IconBadge(symbol: note.symbol, tint: tint, size: 30)

                VStack(alignment: .leading, spacing: 4) {
                    Text(note.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(PWTheme.ink)
                    Text(note.body)
                        .font(.caption)
                        .foregroundStyle(PWTheme.secondaryInk)
                }
            }
        }
    }
}

private struct ProductBar: View {
    let product: TopProduct
    let maxRevenue: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(product.product)
                    .font(.subheadline.weight(.medium))
                    .lineLimit(1)
                Spacer()
                Text(Currency.kes(product.revenue))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
            }

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(PWTheme.softLine)
                    Capsule()
                        .fill(PWTheme.sky)
                        .frame(width: max(8, proxy.size.width * product.revenue / max(maxRevenue, 1)))
                }
            }
            .frame(height: 8)
        }
    }
}

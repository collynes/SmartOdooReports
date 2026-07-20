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
                    ownerSnapshot
                    insights
                    topProducts
                }
                .padding(18)
            }
            .background(PWTheme.background.ignoresSafeArea())
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

    @ViewBuilder
    private var ownerSnapshot: some View {
        if state.hasLiveData {
            kpis
            targetCard
        } else {
            EmptyStateView(
                symbol: "chart.line.uptrend.xyaxis",
                title: "Your owner snapshot is ready when you sign in",
                message: "Revenue, orders, stock value, and target progress stay hidden until live data loads."
            )
        }
    }

    private var hero: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Good day\(state.userName.map { ", \($0)" } ?? "")")
                .font(.title2.weight(.bold))
                .foregroundStyle(PWTheme.ink)
            Text("Here is what changed in the shop today.")
                .font(.subheadline)
                .foregroundStyle(PWTheme.secondaryInk)
            HStack(spacing: 10) {
                Label(state.hasLiveData ? "Live data" : "Not connected", systemImage: state.hasLiveData ? "checkmark.circle.fill" : "wifi.slash")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background((state.hasLiveData ? PWTheme.mint : PWTheme.honey).opacity(0.16))
                    .foregroundStyle(state.hasLiveData ? PWTheme.mint : PWTheme.honey)
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
        if state.hasLiveData == false {
            SoftCard {
                HStack(alignment: .center, spacing: 14) {
                    IconBadge(symbol: "lock.open.fill", tint: PWTheme.sky, size: 34)

                    VStack(alignment: .leading, spacing: 3) {
                        Text("Sign in to load live data")
                            .font(.subheadline.weight(.semibold))
                        Text(state.notice ?? "No business data is shown until the server responds.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    Button("Sign in") {
                        showingSignIn = true
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
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

                Text("\(Currency.kes(state.dashboard.revenueMonth)) of \(Currency.kes(AppState.monthlyRevenueTarget))")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
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

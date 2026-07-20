import SwiftUI

struct StockView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if state.lowStock.isEmpty {
                        LiveDataEmptyState(
                            hasLiveData: state.hasLiveData,
                            liveSymbol: "checkmark.seal.fill",
                            liveTitle: "Stock looks steady",
                            liveMessage: "No low-stock items are showing right now.",
                            waitingTitle: "Waiting for live data",
                            waitingMessage: "Low-stock items will appear after sign-in."
                        )
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    } else {
                        ForEach(state.lowStock) { product in
                            LowStockRow(product: product)
                        }
                    }
                } header: {
                    Text("Low stock")
                } footer: {
                    Text("Items at five units or fewer are surfaced first so ordering can stay ahead of demand.")
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(PWTheme.background)
            .navigationTitle("Stock")
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
}

private struct LowStockRow: View {
    let product: LowStockProduct

    var body: some View {
        BusinessListRow(
            title: product.name,
            subtitle: Currency.kes(product.salePrice)
        ) {
            IconBadge(symbol: product.qtyOnHand <= 0 ? "exclamationmark" : "shippingbox", tint: tint, size: 42)
        } trailing: {
            VStack(alignment: .trailing, spacing: 2) {
                Text(product.qtyOnHand, format: .number.precision(.fractionLength(0...1)))
                    .font(.headline.weight(.bold))
                    .foregroundStyle(tint)
                Text("left")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var tint: Color {
        product.qtyOnHand <= 0 ? PWTheme.coral : PWTheme.honey
    }
}

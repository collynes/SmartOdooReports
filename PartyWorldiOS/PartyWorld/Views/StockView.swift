import SwiftUI

struct StockView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if state.lowStock.isEmpty {
                        EmptyStateView(
                            symbol: "checkmark.seal.fill",
                            title: "Stock looks steady",
                            message: "No low-stock items are showing right now."
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
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(tint.opacity(0.15))
                Image(systemName: product.qtyOnHand <= 0 ? "exclamationmark" : "shippingbox")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(tint)
            }
            .frame(width: 42, height: 42)

            VStack(alignment: .leading, spacing: 3) {
                Text(product.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(PWTheme.ink)
                    .lineLimit(2)
                Text(Currency.kes(product.salePrice))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(product.qtyOnHand, format: .number.precision(.fractionLength(0...1)))
                    .font(.headline.weight(.bold))
                    .foregroundStyle(tint)
                Text("left")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 6)
    }

    private var tint: Color {
        product.qtyOnHand <= 0 ? PWTheme.coral : PWTheme.honey
    }
}

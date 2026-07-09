import SwiftUI

struct SalesView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(state.sales) { sale in
                        SaleRow(sale: sale)
                    }
                } header: {
                    Text("Recent orders")
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(PWTheme.background)
            .navigationTitle("Sales")
            .overlay {
                if state.sales.isEmpty {
                    EmptyStateView(
                        symbol: "receipt",
                        title: "No sales in this range",
                        message: "Recent confirmed sales will appear here."
                    )
                    .padding()
                }
            }
        }
    }
}

private struct SaleRow: View {
    let sale: SaleOrder

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "bag.fill")
                .foregroundStyle(PWTheme.mint)
                .frame(width: 36, height: 36)
                .background(PWTheme.mint.opacity(0.14))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 4) {
                Text(sale.customer?.isEmpty == false ? sale.customer! : "Customer")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(PWTheme.ink)
                Text("\(sale.name) · \(shortDate(sale.dateOrder))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Text(Currency.kes(sale.amountTotal))
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.ink)
        }
        .padding(.vertical, 6)
    }

    private func shortDate(_ value: String) -> String {
        value.replacingOccurrences(of: "T", with: " ").split(separator: " ").first.map(String.init) ?? value
    }
}

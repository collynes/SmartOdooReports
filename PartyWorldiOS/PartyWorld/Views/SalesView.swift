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
            .refreshable { await state.refresh() }
            .navigationTitle("Sales")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await state.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(state.isLoading)
                    .accessibilityLabel("Refresh")
                }
            }
            .overlay {
                if state.sales.isEmpty {
                    LiveDataEmptyState(
                        hasLiveData: state.hasLiveData,
                        liveSymbol: "receipt",
                        liveTitle: "No sales in this range",
                        liveMessage: "Recent confirmed sales will appear here.",
                        waitingTitle: "Waiting for live data",
                        waitingMessage: "Recent confirmed sales will load after sign-in."
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
        BusinessListRow(
            title: sale.customer?.isEmpty == false ? sale.customer! : "Customer",
            subtitle: "\(sale.name) · \(shortDate(sale.dateOrder))"
        ) {
            IconBadge(symbol: "bag.fill", tint: PWTheme.mint, size: 36)
        } trailing: {
            Text(Currency.kes(sale.amountTotal))
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.ink)
        }
    }

    private func shortDate(_ value: String) -> String {
        value.replacingOccurrences(of: "T", with: " ").split(separator: " ").first.map(String.init) ?? value
    }
}

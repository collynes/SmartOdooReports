import SwiftUI

struct CustomersView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if state.customers.isEmpty {
                        LiveDataEmptyState(
                            hasLiveData: state.hasLiveData,
                            liveSymbol: "person.2",
                            liveTitle: "No customers yet",
                            liveMessage: "Customers with confirmed sales will appear here.",
                            waitingTitle: "Waiting for live data",
                            waitingMessage: "Top customers will load after sign-in."
                        )
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    } else {
                        ForEach(state.customers) { customer in
                            CustomerRow(customer: customer)
                        }
                    }
                } header: {
                    Text("Top customers")
                } footer: {
                    Text("Sorted by lifetime spend from confirmed sales.")
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(PWTheme.background)
            .navigationTitle("Customers")
        }
    }
}

private struct CustomerRow: View {
    let customer: Customer

    var body: some View {
        BusinessListRow(
            title: customer.name,
            subtitle: "\(customer.totalOrders) orders"
        ) {
            Text(initials)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.lavender)
                .frame(width: 40, height: 40)
                .background(PWTheme.lavender.opacity(0.14))
                .clipShape(Circle())
        } trailing: {
            Text(Currency.kes(customer.totalSpent))
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.ink)
        }
    }

    private var initials: String {
        let parts = customer.name.split(separator: " ")
        let letters = parts.prefix(2).compactMap { $0.first }
        return letters.map(String.init).joined().uppercased()
    }
}

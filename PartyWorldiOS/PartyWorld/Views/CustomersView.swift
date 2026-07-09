import SwiftUI

struct CustomersView: View {
    @Environment(AppState.self) private var state

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(state.customers) { customer in
                        CustomerRow(customer: customer)
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
        HStack(spacing: 12) {
            Text(initials)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.lavender)
                .frame(width: 40, height: 40)
                .background(PWTheme.lavender.opacity(0.14))
                .clipShape(Circle())

            VStack(alignment: .leading, spacing: 4) {
                Text(customer.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(PWTheme.ink)
                Text("\(customer.totalOrders) orders")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Text(Currency.kes(customer.totalSpent))
                .font(.subheadline.weight(.bold))
                .foregroundStyle(PWTheme.ink)
        }
        .padding(.vertical, 6)
    }

    private var initials: String {
        let parts = customer.name.split(separator: " ")
        let letters = parts.prefix(2).compactMap { $0.first }
        return letters.map(String.init).joined().uppercased()
    }
}

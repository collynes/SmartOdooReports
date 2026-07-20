import SwiftUI

struct RootView: View {
    @Environment(AppState.self) private var state
    @State private var showingSignIn = false
    @State private var showingSettings = false

    var body: some View {
        TabView {
            DashboardView(showingSignIn: $showingSignIn, showingSettings: $showingSettings)
                .tabItem {
                    Label("Today", systemImage: "sun.max.fill")
                }

            AlertsView()
                .tabItem {
                    Label("Alerts", systemImage: "bell.badge.fill")
                }
                .badge(state.ownerAlerts.filter { $0.priority != .info }.count)

            StockView()
                .tabItem {
                    Label("Stock", systemImage: "shippingbox.fill")
                }

            SalesView()
                .tabItem {
                    Label("Sales", systemImage: "creditcard.fill")
                }

            CustomersView()
                .tabItem {
                    Label("Customers", systemImage: "person.2.fill")
                }
        }
        .tint(PWTheme.coral)
        .sheet(isPresented: $showingSignIn) {
            SignInView()
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showingSettings) {
            SettingsView(showingSignIn: $showingSignIn)
        }
        .task {
            await state.refresh()
        }
    }
}

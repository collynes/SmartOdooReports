import SwiftUI

enum AppTab: Hashable {
    case today
    case alerts
    case stock
    case sales
    case customers

    init(route: String?) {
        switch route {
        case "stock": self = .stock
        case "sales": self = .sales
        case "customers": self = .customers
        case "dashboard": self = .today
        default: self = .alerts
        }
    }
}

struct RootView: View {
    @Environment(AppState.self) private var state
    @Environment(\.scenePhase) private var scenePhase
    @State private var showingSignIn = false
    @State private var showingSettings = false
    @State private var selectedTab: AppTab = .today

    var body: some View {
        TabView(selection: $selectedTab) {
            DashboardView(showingSignIn: $showingSignIn, showingSettings: $showingSettings)
                .tabItem {
                    Label("Today", systemImage: "sun.max.fill")
                }
                .tag(AppTab.today)

            AlertsView { route in
                selectedTab = AppTab(route: route)
            }
                .tabItem {
                    Label("Alerts", systemImage: "bell.badge.fill")
                }
                .badge(state.ownerAlerts.filter { $0.priority != .info }.count)
                .tag(AppTab.alerts)

            StockView()
                .tabItem {
                    Label("Stock", systemImage: "shippingbox.fill")
                }
                .tag(AppTab.stock)

            SalesView()
                .tabItem {
                    Label("Sales", systemImage: "creditcard.fill")
                }
                .tag(AppTab.sales)

            CustomersView()
                .tabItem {
                    Label("Customers", systemImage: "person.2.fill")
                }
                .tag(AppTab.customers)
        }
        .tint(PWTheme.coral)
        .sheet(isPresented: $showingSignIn) {
            SignInView()
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showingSettings) {
            SettingsView()
        }
        .task {
            await state.refreshIfNeeded(maxAge: 0)
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active else { return }
            Task { await state.refreshIfNeeded() }
        }
    }
}

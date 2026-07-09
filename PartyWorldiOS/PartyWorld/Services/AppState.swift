import Foundation
import Observation

@MainActor
@Observable
final class AppState {
    static let monthlyRevenueTarget: Double = 800_000

    var baseURLText: String {
        didSet { UserDefaults.standard.set(baseURLText, forKey: Keys.baseURL) }
    }
    var accessToken: String? {
        didSet { UserDefaults.standard.set(accessToken, forKey: Keys.accessToken) }
    }
    var userName: String? {
        didSet { UserDefaults.standard.set(userName, forKey: Keys.userName) }
    }
    var dashboard = DemoData.dashboard
    var lowStock = DemoData.lowStock
    var sales = DemoData.sales
    var customers = DemoData.customers
    var ownerAlerts = DemoData.ownerAlerts
    var isLoading = false
    var lastUpdated: Date?
    var notice: String?
    var isUsingDemoData = true
    var notificationsEnabled = UserDefaults.standard.bool(forKey: Keys.notificationsEnabled) {
        didSet { UserDefaults.standard.set(notificationsEnabled, forKey: Keys.notificationsEnabled) }
    }

    private let api = APIClient()
    private let notificationCenter = NotificationCenterService()

    init() {
        self.baseURLText = UserDefaults.standard.string(forKey: Keys.baseURL) ?? "http://3.78.133.72:1989"
        self.accessToken = UserDefaults.standard.string(forKey: Keys.accessToken)
        self.userName = UserDefaults.standard.string(forKey: Keys.userName)
    }

    var isSignedIn: Bool {
        accessToken != nil
    }

    var baseURL: URL? {
        URL(string: baseURLText.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    var monthlyTargetProgress: Double {
        min(dashboard.revenueMonth / Self.monthlyRevenueTarget, 1)
    }

    var insightNotes: [InsightNote] {
        var notes: [InsightNote] = []

        if dashboard.lowStockAlerts > 0 {
            notes.append(InsightNote(
                title: "\(dashboard.lowStockAlerts) items need a stock check",
                body: "Start with the items at zero quantity before the weekend rush.",
                tone: .attention,
                symbol: "exclamationmark.triangle.fill"
            ))
        }

        let remaining = max(Self.monthlyRevenueTarget - dashboard.revenueMonth, 0)
        if remaining > 0 {
            notes.append(InsightNote(
                title: "\(Currency.kes(remaining)) left for the monthly target",
                body: "The shop is \(Int(monthlyTargetProgress * 100))% of the way to \(Currency.kes(Self.monthlyRevenueTarget)).",
                tone: .helpful,
                symbol: "target"
            ))
        } else {
            notes.append(InsightNote(
                title: "Monthly target is covered",
                body: "Now protect margin and keep high-moving stock available.",
                tone: .positive,
                symbol: "checkmark.seal.fill"
            ))
        }

        if let top = dashboard.topProductsMonth.first {
            notes.append(InsightNote(
                title: "\(top.product) is leading this month",
                body: "It has brought in \(Currency.kes(top.revenue)); keep it visible and easy to reorder.",
                tone: .positive,
                symbol: "chart.line.uptrend.xyaxis"
            ))
        }

        return notes
    }

    func signIn(username: String, password: String) async throws {
        guard let baseURL else { throw APIError.invalidURL }
        isLoading = true
        defer { isLoading = false }

        let response = try await api.login(baseURL: baseURL, username: username, password: password)
        accessToken = response.accessToken
        userName = response.name
        notice = "Signed in as \(response.name)."
        await refresh()
    }

    func signOut() {
        accessToken = nil
        userName = nil
        isUsingDemoData = true
        dashboard = DemoData.dashboard
        lowStock = DemoData.lowStock
        sales = DemoData.sales
        customers = DemoData.customers
        ownerAlerts = DemoData.ownerAlerts
        notice = "Signed out. Demo data is showing."
    }

    func enableOwnerNotifications() async {
        notificationsEnabled = await notificationCenter.requestAuthorization()
        notice = notificationsEnabled ? "Owner notifications are enabled." : "Notifications were not enabled."
    }

    func refresh() async {
        guard let baseURL, let accessToken else {
            isUsingDemoData = true
            notice = "Connect to the Party World API when the local stack is ready."
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            async let dashboardResponse = api.dashboard(baseURL: baseURL, token: accessToken)
            async let lowStockResponse = api.lowStock(baseURL: baseURL, token: accessToken)
            async let salesResponse = api.sales(baseURL: baseURL, token: accessToken)
            async let customersResponse = api.customers(baseURL: baseURL, token: accessToken)
            async let ownerAlertsResponse = api.ownerNotifications(baseURL: baseURL, token: accessToken)

            dashboard = try await dashboardResponse
            lowStock = try await lowStockResponse.results
            sales = try await salesResponse.results
            customers = try await customersResponse.results
            ownerAlerts = try await ownerAlertsResponse.results
            lastUpdated = Date()
            isUsingDemoData = false
            notice = "Updated just now."
            if notificationsEnabled {
                await notificationCenter.postUrgentAlerts(ownerAlerts)
            }
        } catch {
            isUsingDemoData = true
            notice = error.localizedDescription
        }
    }

    private enum Keys {
        static let baseURL = "partyworld.baseURL"
        static let accessToken = "partyworld.accessToken"
        static let userName = "partyworld.userName"
        static let notificationsEnabled = "partyworld.notificationsEnabled"
    }
}

enum Currency {
    private static let formatter: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = "KES"
        formatter.currencySymbol = "KES "
        formatter.maximumFractionDigits = 0
        return formatter
    }()

    static func kes(_ value: Double) -> String {
        formatter.string(from: NSNumber(value: value)) ?? "KES \(Int(value))"
    }
}

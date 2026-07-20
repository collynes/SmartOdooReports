import Foundation
import Observation

@MainActor
@Observable
final class AppState {
    static let fallbackMonthlyRevenueTarget: Double = 800_000

    var baseURLText: String {
        didSet { UserDefaults.standard.set(baseURLText, forKey: Keys.baseURL) }
    }
    var accessToken: String? {
        didSet { keychain.set(accessToken, for: Keys.accessToken) }
    }
    var userName: String? {
        didSet { UserDefaults.standard.set(userName, forKey: Keys.userName) }
    }
    var dashboard = DashboardSnapshot.empty
    var lowStock: [LowStockProduct] = []
    var sales: [SaleOrder] = []
    var customers: [Customer] = []
    var ownerAlerts: [OwnerAlert] = []
    var isLoading = false
    var lastUpdated: Date?
    var notice: String?
    var hasLiveData = false
    var isDataStale = false
    var notificationsEnabled = UserDefaults.standard.bool(forKey: Keys.notificationsEnabled) {
        didSet { UserDefaults.standard.set(notificationsEnabled, forKey: Keys.notificationsEnabled) }
    }

    private let api = APIClient()
    private let notificationCenter = NotificationCenterService()
    private let keychain = KeychainStore()

    init() {
        self.baseURLText = UserDefaults.standard.string(forKey: Keys.baseURL) ?? "https://partyworld.co.ke"
        self.accessToken = KeychainStore().string(for: Keys.accessToken)
        self.userName = UserDefaults.standard.string(forKey: Keys.userName)
        UserDefaults.standard.removeObject(forKey: Keys.accessToken)
    }

    var isSignedIn: Bool {
        accessToken != nil
    }

    var baseURL: URL? {
        URL(string: baseURLText.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    var monthlyTargetProgress: Double {
        min(dashboard.revenueMonth / monthlyRevenueTarget, 1)
    }

    var monthlyRevenueTarget: Double {
        dashboard.monthlyRevenueTarget ?? Self.fallbackMonthlyRevenueTarget
    }

    var insightNotes: [InsightNote] {
        guard hasLiveData else { return [] }

        var notes: [InsightNote] = []

        if dashboard.lowStockAlerts > 0 {
            notes.append(InsightNote(
                title: "\(dashboard.lowStockAlerts) items need a stock check",
                body: "Start with the items at zero quantity before the weekend rush.",
                tone: .attention,
                symbol: "exclamationmark.triangle.fill"
            ))
        }

        let remaining = max(monthlyRevenueTarget - dashboard.revenueMonth, 0)
        if remaining > 0 {
            notes.append(InsightNote(
                title: "\(Currency.kes(remaining)) left for the monthly target",
                body: "The shop is \(Int(monthlyTargetProgress * 100))% of the way to \(Currency.kes(monthlyRevenueTarget)).",
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
        resetBusinessData()
        notice = "Signed out."
    }

    func enableOwnerNotifications() async {
        notificationsEnabled = await notificationCenter.requestAuthorization()
        notice = notificationsEnabled ? "Owner notifications are enabled." : "Notifications were not enabled."
    }

    func refresh() async {
        guard let baseURL, let accessToken else {
            hasLiveData = false
            notice = "Sales, stock, customers, and owner alerts will appear here."
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            do {
                let summary = try await api.mobileSummary(baseURL: baseURL, token: accessToken)
                apply(summary)
            } catch APIError.badResponse(404) {
                try await loadLegacyEndpoints(baseURL: baseURL, token: accessToken)
            }

            markRefreshComplete()
        } catch {
            if lastUpdated == nil {
                resetBusinessData()
            } else {
                isDataStale = true
            }
            if case APIError.unauthorized = error {
                self.accessToken = nil
                self.userName = nil
            }
            notice = error.localizedDescription
        }
    }

    func refreshIfNeeded(maxAge: TimeInterval = 300) async {
        guard let lastUpdated else {
            await refresh()
            return
        }
        if Date().timeIntervalSince(lastUpdated) >= maxAge {
            await refresh()
        }
    }

    private func apply(_ summary: MobileSummaryResponse) {
        dashboard = summary.dashboard
        lowStock = summary.lowStock.results
        sales = summary.sales.results
        customers = summary.customers.results
        ownerAlerts = summary.ownerNotifications.results
    }

    private func loadLegacyEndpoints(baseURL: URL, token: String) async throws {
        async let dashboardResponse = api.dashboard(baseURL: baseURL, token: token)
        async let lowStockResponse = api.lowStock(baseURL: baseURL, token: token)
        async let salesResponse = api.sales(baseURL: baseURL, token: token)
        async let customersResponse = api.customers(baseURL: baseURL, token: token)
        async let ownerAlertsResponse = api.ownerNotifications(baseURL: baseURL, token: token)

        dashboard = try await dashboardResponse
        lowStock = try await lowStockResponse.results
        sales = try await salesResponse.results
        customers = try await customersResponse.results
        ownerAlerts = try await ownerAlertsResponse.results
    }

    private func markRefreshComplete() {
        lastUpdated = Date()
        hasLiveData = true
        isDataStale = false
        notice = "Updated just now."
        if notificationsEnabled {
            Task { await notificationCenter.postUrgentAlerts(ownerAlerts) }
        }
    }

    private func resetBusinessData() {
        dashboard = .empty
        lowStock = []
        sales = []
        customers = []
        ownerAlerts = []
        hasLiveData = false
        isDataStale = false
        lastUpdated = nil
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

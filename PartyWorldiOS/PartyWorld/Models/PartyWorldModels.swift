import Foundation

struct DashboardSnapshot: Codable, Equatable {
    let date: String
    let revenueToday: Double
    let revenueMonth: Double
    let ordersToday: Int
    let stockValue: Double
    let lowStockAlerts: Int
    let topProductsMonth: [TopProduct]

    enum CodingKeys: String, CodingKey {
        case date
        case revenueToday = "revenue_today"
        case revenueMonth = "revenue_month"
        case ordersToday = "orders_today"
        case stockValue = "stock_value"
        case lowStockAlerts = "low_stock_alerts"
        case topProductsMonth = "top_products_month"
    }

    static let empty = DashboardSnapshot(
        date: "",
        revenueToday: 0,
        revenueMonth: 0,
        ordersToday: 0,
        stockValue: 0,
        lowStockAlerts: 0,
        topProductsMonth: []
    )
}

struct TopProduct: Codable, Identifiable, Equatable {
    var id: String { product }
    let product: String
    let qtySold: Double
    let revenue: Double

    enum CodingKeys: String, CodingKey {
        case product
        case qtySold = "qty_sold"
        case revenue
    }
}

struct PagedResponse<T: Codable & Equatable>: Codable, Equatable {
    let count: Int
    let offset: Int?
    let results: [T]
}

struct LowStockProduct: Codable, Identifiable, Equatable {
    let id: Int
    let name: String
    let qtyOnHand: Double
    let salePrice: Double

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case qtyOnHand = "qty_on_hand"
        case salePrice = "sale_price"
    }
}

struct SaleOrder: Codable, Identifiable, Equatable {
    let id: Int
    let name: String
    let dateOrder: String
    let customer: String?
    let amountTotal: Double
    let amountTax: Double?
    let state: String

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case dateOrder = "date_order"
        case customer
        case amountTotal = "amount_total"
        case amountTax = "amount_tax"
        case state
    }
}

struct Customer: Codable, Identifiable, Equatable {
    let id: Int
    let name: String
    let phone: String?
    let email: String?
    let street: String?
    let totalOrders: Int
    let totalSpent: Double

    enum CodingKeys: String, CodingKey {
        case id
        case name
        case phone
        case email
        case street
        case totalOrders = "total_orders"
        case totalSpent = "total_spent"
    }
}

struct OwnerNotificationsResponse: Codable, Equatable {
    let date: String
    let count: Int
    let criticalCount: Int
    let warningCount: Int
    let results: [OwnerAlert]

    enum CodingKeys: String, CodingKey {
        case date
        case count
        case criticalCount = "critical_count"
        case warningCount = "warning_count"
        case results
    }
}

struct OwnerAlert: Codable, Identifiable, Equatable {
    enum Priority: String, Codable {
        case critical
        case warning
        case info
    }

    let id: String
    let category: String
    let priority: Priority
    let title: String
    let body: String
    let metricLabel: String?
    let metricValue: Double?
    let actionLabel: String?
    let route: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case category
        case priority
        case title
        case body
        case metricLabel = "metric_label"
        case metricValue = "metric_value"
        case actionLabel = "action_label"
        case route
        case createdAt = "created_at"
    }
}

struct LoginRequest: Encodable {
    let username: String
    let password: String
}

struct LoginResponse: Decodable {
    let accessToken: String
    let tokenType: String
    let expiresInDays: Int
    let userID: Int
    let name: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
        case expiresInDays = "expires_in_days"
        case userID = "user_id"
        case name
    }
}

struct InsightNote: Identifiable, Equatable {
    enum Tone {
        case helpful
        case attention
        case positive
    }

    let id = UUID()
    let title: String
    let body: String
    let tone: Tone
    let symbol: String
}

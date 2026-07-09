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

enum DemoData {
    static let dashboard = DashboardSnapshot(
        date: "2026-07-08",
        revenueToday: 18450,
        revenueMonth: 286700,
        ordersToday: 12,
        stockValue: 742300,
        lowStockAlerts: 8,
        topProductsMonth: [
            TopProduct(product: "Number Balloons", qtySold: 96, revenue: 38400),
            TopProduct(product: "Birthday Candles", qtySold: 74, revenue: 22200),
            TopProduct(product: "Gift Bags", qtySold: 61, revenue: 20130),
            TopProduct(product: "Foil Curtains", qtySold: 38, revenue: 19000),
            TopProduct(product: "Confetti Poppers", qtySold: 33, revenue: 16500)
        ]
    )

    static let lowStock = [
        LowStockProduct(id: 1, name: "Gold Number 1 Balloon", qtyOnHand: 0, salePrice: 450),
        LowStockProduct(id: 2, name: "Blue Curling Ribbon", qtyOnHand: 2, salePrice: 180),
        LowStockProduct(id: 3, name: "Cake Topper Happy Birthday", qtyOnHand: 3, salePrice: 250),
        LowStockProduct(id: 4, name: "Pastel Gift Bag Large", qtyOnHand: 5, salePrice: 330)
    ]

    static let sales = [
        SaleOrder(id: 101, name: "SO1024", dateOrder: "2026-07-08 11:21:00", customer: "Walk-in Customer", amountTotal: 3650, amountTax: 0, state: "sale"),
        SaleOrder(id: 102, name: "SO1023", dateOrder: "2026-07-08 10:42:00", customer: "Star Events", amountTotal: 12400, amountTax: 0, state: "sale"),
        SaleOrder(id: 103, name: "SO1022", dateOrder: "2026-07-07 17:18:00", customer: "Faith Chepkoech", amountTotal: 2100, amountTax: 0, state: "done")
    ]

    static let customers = [
        Customer(id: 201, name: "Star Events", phone: "+254 700 000 001", email: nil, street: "Nairobi", totalOrders: 9, totalSpent: 84200),
        Customer(id: 202, name: "Walk-in Customer", phone: nil, email: nil, street: nil, totalOrders: 38, totalSpent: 137600),
        Customer(id: 203, name: "Faith Chepkoech", phone: nil, email: nil, street: "Kamkunji", totalOrders: 5, totalSpent: 22650)
    ]

    static let ownerAlerts = [
        OwnerAlert(
            id: "demo-stock-zero",
            category: "stock",
            priority: .critical,
            title: "2 products are out of stock",
            body: "These can block sales today. Review the reorder list before checking slower items.",
            metricLabel: "Out of stock",
            metricValue: 2,
            actionLabel: "Open stock",
            route: "stock",
            createdAt: "2026-07-08T09:00:00Z"
        ),
        OwnerAlert(
            id: "demo-target-daily",
            category: "sales",
            priority: .warning,
            title: "Today is behind the sales pace",
            body: "Today is at KES 18,450 against a daily pace of about KES 12,900. Keep checking progress after lunch.",
            metricLabel: "Today revenue",
            metricValue: 18450,
            actionLabel: "Check sales",
            route: "sales",
            createdAt: "2026-07-08T09:00:00Z"
        ),
        OwnerAlert(
            id: "demo-invoices",
            category: "cashflow",
            priority: .info,
            title: "Customer follow-up may help cash flow",
            body: "A few customer balances are still open. Follow up while the orders are fresh.",
            metricLabel: "Amount due",
            metricValue: 12600,
            actionLabel: "Review customers",
            route: "customers",
            createdAt: "2026-07-08T09:00:00Z"
        )
    ]
}

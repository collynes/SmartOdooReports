import XCTest
@testable import PartyWorld

final class PartyWorldTests: XCTestCase {
    func testDashboardUsesServerTarget() throws {
        let data = Data("""
        {
          "date": "2026-07-20",
          "revenue_today": 10000,
          "revenue_month": 250000,
          "orders_today": 8,
          "stock_value": 400000,
          "low_stock_alerts": 3,
          "top_products_month": [],
          "monthly_revenue_target": 900000
        }
        """.utf8)

        let snapshot = try JSONDecoder().decode(DashboardSnapshot.self, from: data)
        XCTAssertEqual(snapshot.monthlyRevenueTarget, 900_000)
    }

    func testAlertRoutesSelectExpectedTabs() {
        XCTAssertEqual(AppTab(route: "dashboard"), .today)
        XCTAssertEqual(AppTab(route: "stock"), .stock)
        XCTAssertEqual(AppTab(route: "sales"), .sales)
        XCTAssertEqual(AppTab(route: "customers"), .customers)
        XCTAssertEqual(AppTab(route: "expenses"), .alerts)
    }
}

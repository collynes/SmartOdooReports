import Foundation
import UserNotifications

struct NotificationCenterService: Sendable {
    func requestAuthorization() async -> Bool {
        do {
            return try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
        } catch {
            return false
        }
    }

    func postUrgentAlerts(_ alerts: [OwnerAlert]) async {
        let urgent = alerts.filter { $0.priority == .critical || $0.priority == .warning }.prefix(3)
        guard urgent.isEmpty == false else {
            await setBadge(0)
            return
        }

        let center = UNUserNotificationCenter.current()
        let delivered = await center.deliveredNotifications()
        let deliveredIDs = Set(delivered.map(\.request.identifier))
        await setBadge(urgent.count)
        for alert in urgent {
            let identifier = "owner-alert-\(alert.id)"
            guard deliveredIDs.contains(identifier) == false else { continue }
            let content = UNMutableNotificationContent()
            content.title = alert.title
            content.body = alert.body
            content.sound = .default
            content.badge = NSNumber(value: urgent.count)
            content.userInfo = ["route": alert.route ?? ""]

            let request = UNNotificationRequest(
                identifier: identifier,
                content: content,
                trigger: nil
            )
            try? await UNUserNotificationCenter.current().add(request)
        }
    }

    private func setBadge(_ value: Int) async {
        do {
            try await UNUserNotificationCenter.current().setBadgeCount(value)
        } catch {
            // Badge updates are best-effort; the alert list still remains visible in-app.
        }
    }
}

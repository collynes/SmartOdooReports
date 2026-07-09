import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case unauthorized
    case badResponse(Int)
    case noToken

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "The server address is not valid."
        case .unauthorized:
            "Please sign in again."
        case .badResponse(let status):
            "The server returned status \(status)."
        case .noToken:
            "No access token is saved."
        }
    }
}

struct APIClient: Sendable {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func login(baseURL: URL, username: String, password: String) async throws -> LoginResponse {
        var request = try makeRequest(baseURL: baseURL, path: "/auth/login", token: nil)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(LoginRequest(username: username, password: password))
        return try await send(request)
    }

    func dashboard(baseURL: URL, token: String) async throws -> DashboardSnapshot {
        try await get(baseURL: baseURL, path: "/api/v1/dashboard", token: token)
    }

    func lowStock(baseURL: URL, token: String) async throws -> PagedResponse<LowStockProduct> {
        try await get(baseURL: baseURL, path: "/api/v1/stock/low", token: token)
    }

    func sales(baseURL: URL, token: String) async throws -> PagedResponse<SaleOrder> {
        try await get(baseURL: baseURL, path: "/api/v1/sales", token: token)
    }

    func customers(baseURL: URL, token: String) async throws -> PagedResponse<Customer> {
        try await get(baseURL: baseURL, path: "/api/v1/customers", token: token)
    }

    func ownerNotifications(baseURL: URL, token: String) async throws -> OwnerNotificationsResponse {
        try await get(baseURL: baseURL, path: "/api/v1/owner/notifications", token: token)
    }

    private func get<T: Decodable>(baseURL: URL, path: String, token: String) async throws -> T {
        let request = try makeRequest(baseURL: baseURL, path: path, token: token)
        return try await send(request)
    }

    private func makeRequest(baseURL: URL, path: String, token: String?) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidURL
        }

        switch http.statusCode {
        case 200..<300:
            return try JSONDecoder().decode(T.self, from: data)
        case 401:
            throw APIError.unauthorized
        default:
            throw APIError.badResponse(http.statusCode)
        }
    }
}

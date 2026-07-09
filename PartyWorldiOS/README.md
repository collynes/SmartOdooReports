# Party World iOS

A native SwiftUI business intelligence companion for Party World.

The app is intentionally calm, warm, and operational: it opens on a useful KPI snapshot, explains changes in plain language, and keeps frequent actions like low-stock review and sales checks close at hand.

## Run

1. Open `PartyWorld.xcodeproj` in Xcode.
2. Select the `PartyWorld` scheme.
3. Run on an iPhone simulator or device.

The default API base URL is `http://3.78.133.72:1989`, matching the live Reports webapp. You can change it in Settings inside the app.

## Backend

Expected endpoints come from the mobile-compatible API in `SmartOdooReports/app.py` or the standalone FastAPI app in `SmartOdooReports/mobile_api.py`:

- `POST /auth/login`
- `GET /api/v1/dashboard`
- `GET /api/v1/stock/low`
- `GET /api/v1/sales`
- `GET /api/v1/customers`

When the API is unavailable, the app keeps showing realistic demo data so the product direction remains visible while the local database is being restored.

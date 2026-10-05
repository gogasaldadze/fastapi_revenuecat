# iOS integration

The app side with the RevenueCat Swift SDK. Android follows the same order with the Kotlin SDK.

## Rules

1. Configure the SDK before anything calls `logIn`.
2. Call `logIn` with your own user id before showing a paywall, so purchases attach to that id and not to an anonymous one.
3. Call `/subscription/sync` after every purchase and every restore. By default the server only accepts the signed in user's own id, so step 2 is required.
4. Sync again whenever the entitlement turns from inactive to active outside a purchase (renewal while the app was closed, family sharing, another device).
5. Never cache `isPremium` on the device. Show premium UI from `CustomerInfo`, and let the server decide what the server unlocks.

## Configure, then log in

```swift
import RevenueCat

@main
struct MyApp: App {
    init() {
        // The public SDK key (appl_...) is meant to ship in the app.
        // Read it from Info.plist so each build configuration can use its own.
        let key = Bundle.main.object(forInfoDictionaryKey: "RevenueCatAPIKey") as? String ?? ""
        Purchases.configure(withAPIKey: key)
        Purchases.shared.delegate = SubscriptionObserver.shared
    }

    var body: some Scene {
        WindowGroup { RootView() }
    }
}

// After your own sign in completes:
func didSignIn(userId: String) async throws {
    _ = try await Purchases.shared.logIn(userId)
    try await SubscriptionAPI.shared.sync()
}
```

If `logIn` runs before `configure`, it does nothing and purchases land on an anonymous id. A purchase made on an anonymous id before `logIn` still works: `logIn` aliases the anonymous id to your user id in RevenueCat, and the server accepts an id that RevenueCat aliases to the signed in user. Any other id is refused unless the server runs with `REVENUECAT_REQUIRE_USER_ID_MATCH=false`.

## Sync with the server

```swift
struct SyncResponse: Decodable {
    let isPremium: Bool
    enum CodingKeys: String, CodingKey { case isPremium = "is_premium" }
}

final class SubscriptionAPI {
    static let shared = SubscriptionAPI()
    var baseURL = URL(string: "https://api.example.com")!
    var token: () -> String = { "" }

    @discardableResult
    func sync() async throws -> Bool {
        var request = URLRequest(url: baseURL.appending(path: "subscription/sync"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token())", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["app_user_id": Purchases.shared.appUserID])
        let (data, _) = try await URLSession.shared.data(for: request)
        return try JSONDecoder().decode(SyncResponse.self, from: data).isPremium
    }
}
```

The endpoint is idempotent. Calling it too often costs one RevenueCat request; calling it too rarely leaves the server behind. Sending `appUserID` is optional by default, since the server checks the signed in user's id; sending it helps when a purchase was made before `logIn`.

## Purchase and restore

```swift
func buy(_ package: Package) async throws {
    let result = try await Purchases.shared.purchase(package: package)
    guard !result.userCancelled else { return }
    try await SubscriptionAPI.shared.sync()
}

func restore() async throws {
    _ = try await Purchases.shared.restorePurchases()
    // Always sync after a restore, even if the device already shows premium.
    // The point of restore is that something is out of step.
    try await SubscriptionAPI.shared.sync()
}
```

## Sync when the entitlement turns on

```swift
final class SubscriptionObserver: NSObject, PurchasesDelegate {
    static let shared = SubscriptionObserver()
    private var wasActive = false

    func purchases(_ purchases: Purchases, receivedUpdated customerInfo: CustomerInfo) {
        let active = customerInfo.entitlements["premium"]?.isActive == true
        defer { wasActive = active }
        guard active, !wasActive else { return }
        Task { try? await SubscriptionAPI.shared.sync() }
    }
}
```

This catches renewals that happened while the app was closed and purchases made on another device. A purchase in this session triggers it too; the extra sync is harmless.

## What not to do

* Do not store `isPremium` in `UserDefaults` and read it at launch. It survives backups to another Apple ID and can be edited on a jailbroken device. Start as not premium and wait for `customerInfo`.
* Do not send premium state to your server in headers. The server ignores it.
* Do not ship a build with a sandbox override pointed at production. Sandbox purchases are free.
* Use the entitlement identifier, not a product id, to decide access. Product ids change when you add plans; the entitlement does not.

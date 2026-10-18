import Foundation
import Security

enum GeminiKeychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.talk2gemi.gemini",
        kSecAttrAccount as String: "api-key"
    ]
    enum ReadResult {
        case found(String)
        case missing
        case unavailable(OSStatus)
    }
    static func read() -> ReadResult {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &result)
        if status == errSecItemNotFound { return .missing }
        guard status == errSecSuccess else { return .unavailable(status) }
        guard let data = result as? Data, let key = String(data:data,encoding:.utf8), !key.isEmpty else {
            return .unavailable(errSecDecode)
        }
        return .found(key)
    }
    static func save(_ value: String) throws {
        let data = Data(value.utf8)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String:data] as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrLabel as String] = "talk2gemi Gemini API Key"
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw NSError(domain:NSOSStatusErrorDomain,code:Int(added)) }
        } else if status != errSecSuccess { throw NSError(domain:NSOSStatusErrorDomain,code:Int(status)) }
    }
}

// Main-queue session cache: never serializes the key outside Keychain.
// Missing/denied results are cached too, so reconnect cannot produce a prompt loop.
final class GeminiCredentialSession {
    private var loaded = false
    private(set) var readCount = 0
    private var value: GeminiKeychain.ReadResult = .unavailable(errSecInteractionNotAllowed)
    private let reader: () -> GeminiKeychain.ReadResult
    private let writer: (String) throws -> Void
    init(reader: @escaping () -> GeminiKeychain.ReadResult = GeminiKeychain.read,
         writer: @escaping (String) throws -> Void = GeminiKeychain.save) {
        self.reader = reader; self.writer = writer
    }
    var cachedKey: String? {
        if case .found(let key) = value { return key }
        return nil
    }
    func readOnce() -> GeminiKeychain.ReadResult {
        if !loaded {
            readCount += 1
            loaded = true  // Set before any system authentication UI/reentrancy.
            value = reader()
        }
        return value
    }
    func save(_ key: String) throws {
        try writer(key)
        value = .found(key); loaded = true
    }
}

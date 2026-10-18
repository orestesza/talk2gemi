import AppKit
import CoreBluetooth
import ServiceManagement

// CoreBluetooth callbacks and IPC dispatch both run on the main queue. No audio
// codec or cloud requests run here; bounded GATT fragments go to the relay.
final class Companion: NSObject, NSApplicationDelegate, CBPeripheralManagerDelegate {
    let serviceID = CBUUID(string: "834F0001-3B4B-4B31-8F29-74CCCF3D4430")
    let identityID = CBUUID(string: "834F0002-3B4B-4B31-8F29-74CCCF3D4430")
    let upID = CBUUID(string: "834F0003-3B4B-4B31-8F29-74CCCF3D4430")
    let downID = CBUUID(string: "834F0004-3B4B-4B31-8F29-74CCCF3D4430")
    var probePSM: CBL2CAPPSM = 0
    var probeStreams = [String: CoCProbeEcho]()
    var manager: CBPeripheralManager!
    var down: CBMutableCharacteristic!
    var identity = Data()
    var socket: URLSessionWebSocketTask?
    var item: NSStatusItem!
    var status = "Starting"
    var root = ""
    var stateRoot = ""
    var portable = false
    var migrationMode = false
    var askedForKey = false
    let credentialSession = GeminiCredentialSession()
    var relay: Process?
    var centrals = [String: CBCentral]()
    var blockedPeers = Set<String>()
    var partial = [String: (UInt16, Int, Data)]()
    var outgoing = [(String, Data)]()
    var fragmentID: UInt16 = 0
    var connected = false
    var hostName = "Mac"
    var retry: Timer?
    var pairingWindow: NSWindow?
    var pendingPair: (peer: String, code: String)?
    var statusLine = NSMenuItem(title: "Starting", action: nil, keyEquivalent: "")

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSLog("talk2gemi starting")
        let args = CommandLine.arguments
        if let resources = Bundle.main.resourceURL,
           FileManager.default.fileExists(atPath:resources.appendingPathComponent("server/relay_server.py").path) {
            root = resources.path
            portable = true
        } else if let i = args.firstIndex(of: "--project"), args.count > i + 1 {
            root = args[i + 1]
            UserDefaults.standard.set(root, forKey: "projectRoot")
            migrationMode = args.contains("--migration")
            UserDefaults.standard.set(migrationMode, forKey: "migrationMode")
        } else {
            root = UserDefaults.standard.string(forKey: "projectRoot") ?? ""
            migrationMode = UserDefaults.standard.bool(forKey: "migrationMode")
        }
        stateRoot = ProcessInfo.processInfo.environment["TALK2GEMI_STATE_DIR"] ?? ((portable || migrationMode)
            ? FileManager.default.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("talk2gemi").path
            : root+"/.runtime")
        try? FileManager.default.createDirectory(atPath:stateRoot,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            var icon: NSImage? = nil
            if let path = Bundle.main.path(forResource: "owl_status", ofType: "png") {
                icon = NSImage(contentsOfFile: path)
            } else if let img = Bundle.main.image(forResource: "owl_status") {
                icon = img
            } else {
                let devPath = URL(fileURLWithPath: #file).deletingLastPathComponent().appendingPathComponent("owl_status.png").path
                if FileManager.default.fileExists(atPath: devPath) {
                    icon = NSImage(contentsOfFile: devPath)
                }
            }
            if let icon = icon {
                icon.size = NSSize(width: 18, height: 18)
                icon.isTemplate = true
                button.image = icon
                button.imagePosition = .imageOnly
            } else {
                button.title = "T2G"
            }
        }
        let menu = NSMenu()
        menu.addItem(statusLine)
        menu.addItem(NSMenuItem(title: "Open control panel", action: #selector(openPanel), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Conversation history", action: #selector(openHistory), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Restart relay service", action: #selector(restartRelay), keyEquivalent: "r"))
        menu.addItem(NSMenuItem(title: "Start relay service", action: #selector(startRelay), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Gemini API Key…", action: #selector(configureKey), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Start at login", action: #selector(toggleLogin(_:)), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit companion", action: #selector(quit), keyEquivalent: "q"))
        for entry in menu.items { entry.target = self }
        item.menu = menu
        setStatus("Requesting Bluetooth access")
        manager = CBPeripheralManager(delegate: self, queue: .main,
                                      options: [CBPeripheralManagerOptionShowPowerAlertKey: true])
        setStatus("Connecting to relay")
        connect()
        if portable || migrationMode || ProcessInfo.processInfo.environment["TALK2GEMI_AUTOSTART_RELAY"] == "1" { startRelay() }
    }

    func setStatus(_ text: String) {
        status = text; statusLine.title = text
        NSLog("talk2gemi status: %@", text)
        if !root.isEmpty {
            let value: [String: Any] = ["status":text,"relay_connected":connected,"devices":centrals.count,"timestamp":Date().timeIntervalSince1970,"keychain_reads":credentialSession.readCount,"key_cached":credentialSession.cachedKey != nil]
            if let data = try? JSONSerialization.data(withJSONObject:value) {
                try? data.write(to:URL(fileURLWithPath:stateRoot+"/companion-status.json"),options:.atomic)
            }
        }
    }
    @objc func openHistory() { NSWorkspace.shared.open(URL(string: "http://localhost:8765/history")!) }
    @objc func openPanel() { NSWorkspace.shared.open(URL(string: "http://localhost:8765")!) }
    @objc func startRelay() {
        if connected { setStatus("Relay connected"); return }
        if relay?.isRunning == true { return }
        guard FileManager.default.fileExists(atPath: root + "/server/relay_server.py") else {
            setStatus("Project path missing; launch using start_companion.sh"); return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: portable ? root+"/runtime/bin/python3" : "/bin/bash")
        process.arguments = portable ? [root+"/server/relay_server.py"] : [root+"/scripts/python.sh",root+"/server/relay_server.py"]
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        var environment = ProcessInfo.processInfo.environment
        environment["TALK2GEMI_STATE_DIR"] = stateRoot
        environment["PYTHONNOUSERSITE"] = "1"
        if migrationMode {
            environment.removeValue(forKey: "GEMINI_API_KEY")
            environment["TALK2GEMI_LOAD_DOTENV"] = "0"
        }
        if portable { environment["PYTHONPATH"] = root+"/packages" }
        process.environment = environment
        let log = stateRoot + "/companion-relay.log"
        FileManager.default.createFile(atPath: log, contents: nil)
        if let handle = FileHandle(forWritingAtPath: log) {
            process.standardOutput = handle; process.standardError = handle
        }
        process.terminationHandler = { [weak self] p in
            DispatchQueue.main.async { self?.setStatus(p.terminationStatus == 0 ? "Relay stopped" : "Relay stopped; check service log") }
        }
        do { try process.run(); relay = process; setStatus("Relay starting") }
        catch { setStatus("Relay start failed: \(error.localizedDescription)") }
    }
    @objc func configureKey() {
        let field = NSSecureTextField(frame:NSRect(x:0,y:0,width:360,height:24))
        field.placeholderString = "Paste your Gemini API key"
        let alert = NSAlert()
        alert.messageText = "Gemini API Key"
        alert.informativeText = "Saved in this Mac's Keychain. It is never included in the app, device firmware or transfer package. Changes apply after the active conversation."
        alert.accessoryView = field
        alert.addButton(withTitle:"Save to Keychain");alert.addButton(withTitle:"Cancel")
        NSApp.activate(ignoringOtherApps:true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let value = field.stringValue.trimmingCharacters(in:.whitespacesAndNewlines)
        guard value.count >= 10, value.count <= 256, !value.contains(where:{$0.isWhitespace}) else {
            setStatus("API key format invalid; open Gemini API Key again"); return
        }
        do {
            try credentialSession.save(value)
            send(["event":"credentials","key":value])
            setStatus("API key saved to Keychain")
        } catch { setStatus("Keychain save failed: \(error.localizedDescription)") }
        field.stringValue = ""
    }
    @objc func toggleLogin(_ sender: NSMenuItem) {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister(); sender.state = .off
            } else { try SMAppService.mainApp.register(); sender.state = .on }
        } catch { setStatus("Login item: \(error.localizedDescription)") }
    }
    func killRelayProcesses() {
        if let p = relay, p.isRunning {
            p.terminate()
        }
        relay = nil
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "/usr/bin/pkill -9 -f relay_server.py; /usr/sbin/lsof -ti :8765 | /usr/bin/xargs /bin/kill -9 2>/dev/null || true"]
        try? task.run()
        task.waitUntilExit()
    }

    func applicationWillTerminate(_ notification: Notification) {
        killRelayProcesses()
    }

    @objc func restartRelay() {
        setStatus("Restarting relay…")
        socket?.cancel(with: .goingAway, reason: nil)
        connected = false
        killRelayProcesses()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            self?.startRelay()
        }
    }

    @objc func quit() {
        manager.stopAdvertising()
        socket?.cancel(with: .goingAway, reason: nil)
        killRelayProcesses()
        NSApp.terminate(nil)
    }

    func connect() {
        retry?.invalidate(); retry = nil
        let previous = socket; socket = nil
        previous?.cancel(with:.goingAway,reason:nil)
        guard let token = try? String(contentsOfFile: stateRoot + "/bridge.token", encoding: .utf8) else {
            setStatus("Start the relay service first"); reconnect(); return
        }
        var request = URLRequest(url: URL(string: "ws://127.0.0.1:8765/ws/ble-bridge")!)
        request.setValue("Bearer " + token.trimmingCharacters(in: .whitespacesAndNewlines), forHTTPHeaderField: "Authorization")
        socket = URLSession.shared.webSocketTask(with: request)
        socket?.resume(); receive()
    }
    func reconnect() {
        let previous = socket; socket = nil
        previous?.cancel(with:.goingAway,reason:nil)
        pairingWindow?.close();pairingWindow=nil;pendingPair=nil
        probeStreams.values.forEach { $0.close() }; probeStreams.removeAll()
        connected = false; manager?.stopAdvertising(); identity = Data()
        outgoing.removeAll(); partial.removeAll(); centrals.removeAll()
        retry?.invalidate()
        retry = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { [weak self] _ in self?.connect() }
    }
    func send(_ event: [String: Any]) {
        guard let bytes = try? JSONSerialization.data(withJSONObject: event),
              let text = String(data: bytes, encoding: .utf8) else { return }
        guard let connection = socket else { return }
        connection.send(.string(text)) { [weak self] error in
            if error != nil { DispatchQueue.main.async {
                guard let self = self, self.socket === connection else { return }
                self.reconnect()
            } }
        }
    }
    func receive() {
        guard let connection = socket else { return }
        connection.receive { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self, self.socket === connection else { return }
                switch result {
                case .failure:
                    self.setStatus("Relay unavailable; reconnecting"); self.reconnect()
                case .success(let msg):
                    if case .string(let text) = msg, let data = text.data(using: .utf8),
                       let event = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
                        self.handle(event)
                    }
                    if self.socket === connection { self.receive() }
                }
            }
        }
    }
    func handle(_ e: [String: Any]) {
        switch e["event"] as? String {
        case "identity":
            connected = true
            if ProcessInfo.processInfo.environment["TALK2GEMI_COC_PROBE"] != "1" {
                if let key = credentialSession.cachedKey {
                    send(["event":"credentials","key":key])
                } else if !(e["api_configured"] as? Bool ?? false) {
                    switch credentialSession.readOnce() {
                    case .found(let key):
                        send(["event":"credentials","key":key])
                    case .missing:
                        if !askedForKey {
                            askedForKey = true
                            DispatchQueue.main.async { self.configureKey() }
                        }
                    case .unavailable:
                        setStatus("Keychain access not granted · use Gemini API Key…")
                    }
                }
            }
            hostName = e["name"] as? String ?? "Mac"
            identity = (try? JSONSerialization.data(withJSONObject: ["host_id": e["host_id"] ?? "", "name": e["name"] ?? "Mac"])) ?? Data()
            advertise()
        case "credentials_saved": setStatus("API key ready · Bluetooth available")
        case "send":
            if let peer = e["peer"] as? String, let b64 = e["data"] as? String, let data = Data(base64Encoded: b64) {
                guard outgoing.count < 128, data.count <= 2048 else {
                    send(["event": "disconnected", "peer": peer]); return
                }
                fragmentID &+= 1
                let maxSize = max(1, (centrals[peer]?.maximumUpdateValueLength ?? 20) - 6)
                var offset = 0
                while offset < data.count {
                    var chunk = Data()
                    for value in [fragmentID, UInt16(offset), UInt16(data.count)] {
                        chunk.append(UInt8(value & 255)); chunk.append(UInt8(value >> 8))
                    }
                    let end = min(data.count, offset + maxSize)
                    chunk.append(data.subdata(in: offset..<end)); outgoing.append((peer, chunk)); offset = end
                }
                flush()
            }
        case "pairing":
            guard let peer = e["peer"] as? String, let code = e["code"] as? String else { return }
            showPairing(peer:peer,code:code,device:e["device"] as? String ?? "")
        case "disconnect":
            if let peer = e["peer"] as? String {
                if pendingPair?.peer == peer { pairingWindow?.close();pairingWindow=nil;pendingPair=nil }
                blockedPeers.insert(peer);centrals.removeValue(forKey: peer);outgoing.removeAll { $0.0 == peer }
            }
        case "error": setStatus(e["message"] as? String ?? "Bluetooth bridge error")
        default: break
        }
    }

    // A modal runLoop called inside the main dispatch queue blocks later IPC
    // and GATT callbacks. Keep the code visible while the bridge keeps running.
    func showPairing(peer:String,code:String,device:String) {
        pairingWindow?.close()
        pendingPair=(peer,code)
        let window=NSWindow(contentRect:NSRect(x:0,y:0,width:440,height:210),styleMask:[.titled],backing:.buffered,defer:false)
        window.title="Pair talk2gemi";window.isReleasedWhenClosed=false
        let label=NSTextField(wrappingLabelWithString:"Confirm only if your device shows the same code.\nDevice: \(device)")
        label.frame=NSRect(x:24,y:125,width:392,height:55)
        let number=NSTextField(labelWithString:code)
        number.font=NSFont.monospacedDigitSystemFont(ofSize:32,weight:.semibold)
        number.alignment = .center;number.frame=NSRect(x:24,y:70,width:392,height:48)
        let confirm=NSButton(title:"Confirm matching code",target:self,action:#selector(approvePair))
        confirm.frame=NSRect(x:190,y:20,width:225,height:32)
        let cancel=NSButton(title:"Cancel",target:self,action:#selector(cancelPair))
        cancel.frame=NSRect(x:24,y:20,width:130,height:32)
        for view in [label,number,confirm,cancel] { window.contentView?.addSubview(view) }
        pairingWindow=window;window.center();NSApp.activate(ignoringOtherApps:true);window.makeKeyAndOrderFront(nil)
    }
    @objc func approvePair() {
        if let pair=pendingPair { send(["event":"approve","peer":pair.peer,"code":pair.code]) }
        pairingWindow?.close();pairingWindow=nil;pendingPair=nil
    }
    @objc func cancelPair() {
        if let pair=pendingPair { send(["event":"disconnected","peer":pair.peer]) }
        pairingWindow?.close();pairingWindow=nil;pendingPair=nil
    }

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        guard peripheral.state == .poweredOn else {
            setStatus("Bluetooth: \(peripheral.state.rawValue)"); return
        }
        if ProcessInfo.processInfo.environment["TALK2GEMI_COC_PROBE"] == "1" { peripheral.publishL2CAPChannel(withEncryption: true) }
        peripheral.removeAllServices()
        let identityCharacteristic = CBMutableCharacteristic(type: identityID, properties: [.read], value: nil, permissions: [.readable])
        let up = CBMutableCharacteristic(type: upID, properties: [.write, .writeWithoutResponse], value: nil, permissions: [.writeEncryptionRequired])
        down = CBMutableCharacteristic(type: downID, properties: [.notify, .notifyEncryptionRequired], value: nil, permissions: [])
        let service = CBMutableService(type: serviceID, primary: true)
        service.characteristics = [identityCharacteristic, up, down]
        peripheral.add(service)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error = error { setStatus(error.localizedDescription) } else { advertise() }
    }
    func advertise() {
        guard connected, manager.state == .poweredOn, !identity.isEmpty else { return }
        if !manager.isAdvertising { manager.startAdvertising([CBAdvertisementDataServiceUUIDsKey: [serviceID], CBAdvertisementDataLocalNameKey: String(hostName.prefix(20))]) }
        setStatus("Bluetooth ready · \(centrals.count) device(s)")
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        NSLog("BLE read %@ offset=%ld length=%ld",request.characteristic.uuid.uuidString,request.offset,identity.count)
        guard request.characteristic.uuid == identityID else {
            peripheral.respond(to: request, withResult: .invalidOffset); return
        }
        var value = (try? JSONSerialization.jsonObject(with: identity)) as? [String: Any] ?? [:]
        if probePSM != 0 { value["coc_probe_psm"] = Int(probePSM) }
        let bytes = (try? JSONSerialization.data(withJSONObject: value)) ?? identity
        guard request.offset <= bytes.count else { peripheral.respond(to: request, withResult: .invalidOffset); return }
        request.value = bytes.subdata(in: request.offset..<bytes.count)
        peripheral.respond(to: request, withResult: .success)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        blockedPeers.remove(central.identifier.uuidString)
        centrals[central.identifier.uuidString] = central; advertise()
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        let peer = central.identifier.uuidString
        probeStreams.removeValue(forKey: peer)?.close()
        centrals.removeValue(forKey: peer); partial.removeValue(forKey: peer)
        send(["event": "disconnected", "peer": peer]); advertise()
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let first=requests.first else { return }
        var result:CBATTError.Code = .success
        var nextPartial=partial
        var packets=[(String,Data)]()
        for request in requests {
            guard request.characteristic.uuid == upID, let data = request.value, data.count >= 6 else {
                result = .invalidAttributeValueLength;break
            }
            let bytes = [UInt8](data)
            let id = UInt16(bytes[0]) | UInt16(bytes[1]) << 8
            let offset = Int(bytes[2]) | Int(bytes[3]) << 8
            let total = Int(bytes[4]) | Int(bytes[5]) << 8
            let peer = request.central.identifier.uuidString
            if blockedPeers.contains(peer) {
                result = .unlikelyError;break
            }
            if offset == 0 { nextPartial[peer] = (id, total, Data()) }
            guard var state = nextPartial[peer], state.0 == id, state.1 == total, state.2.count == offset,
                  total <= 2048, offset + data.count - 6 <= total else {
                result = .invalidOffset;break
            }
            state.2.append(data.subdata(in: 6..<data.count)); nextPartial[peer] = state
            if state.2.count == total {
                packets.append((peer,state.2));nextPartial.removeValue(forKey:peer)
            }
        }
        // CoreBluetooth requires exactly one response per write callback, using
        // its first request; never partially commit an invalid request batch.
        peripheral.respond(to:first,withResult:result)
        if result == .success {
            partial=nextPartial
            for (peer,data) in packets { send(["event":"packet","peer":peer,"data":data.base64EncodedString()]) }
        }
    }
    func flush() {
        while let (peer, data) = outgoing.first {
            guard let central = centrals[peer] else { outgoing.removeFirst(); continue }
            if !manager.updateValue(data, for: down, onSubscribedCentrals: [central]) { return }
            outgoing.removeFirst()
        }
    }
    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) { flush() }
    func peripheralManager(_ peripheral: CBPeripheralManager, didPublishL2CAPChannel PSM: CBL2CAPPSM, error: Error?) {
        guard error == nil else { NSLog("CoC publish failed: %@", String(describing:error)); return }
        probePSM = PSM; NSLog("CoC probe published encrypted PSM=%u", PSM)
    }
    func peripheralManager(_ peripheral: CBPeripheralManager, didOpen channel: CBL2CAPChannel?, error: Error?) {
        guard let channel = channel, error == nil else { NSLog("CoC open failed: %@", String(describing:error)); return }
        let peer = channel.peer.identifier.uuidString
        guard centrals[peer] != nil, !blockedPeers.contains(peer) else { channel.inputStream.close(); channel.outputStream.close(); return }
        probeStreams[peer]?.close()
        probeStreams[peer] = CoCProbeEcho(channel: channel)
        NSLog("CoC encrypted probe opened")
    }

}



// BEGIN COC PROBE FRAMER - also compiled in the host regression test.
struct CoCProbeFramer {
    enum Failure: Error { case length, capacity, count, truncated }
    private var bytes = Data()
    private(set) var frames = 0
    mutating func append(_ chunk: Data) throws -> [Data] {
        guard bytes.count + chunk.count <= 4096 else { throw Failure.capacity }
        bytes.append(chunk)
        var result = [Data]()
        while bytes.count >= 2 {
            let i = bytes.startIndex
            let n = Int(bytes[i]) | Int(bytes[i+1]) << 8
            guard n == 512 else { throw Failure.length }
            if bytes.count < n+2 { break }
            guard frames < 512 else { throw Failure.count }
            result.append(Data(bytes.prefix(n+2))); bytes.removeFirst(n+2); frames += 1
        }
        return result
    }
    func finish() throws { if !bytes.isEmpty { throw Failure.truncated } }
}
// END COC PROBE FRAMER
final class CoCProbeEcho: NSObject, StreamDelegate {
    let channel: CBL2CAPChannel
    var parser = CoCProbeFramer()
    var output = Data()
    var closed = false, reported = false
    init(channel: CBL2CAPChannel) {
        self.channel = channel; super.init()
        for stream in [channel.inputStream!, channel.outputStream!] {
            stream.delegate = self; stream.schedule(in: .main, forMode: .common); stream.open()
        }
    }
    func close() {
        guard !closed else { return }; closed = true
        for stream in [channel.inputStream!, channel.outputStream!] {
            stream.delegate = nil; stream.close(); stream.remove(from: .main, forMode: .common)
        }
        output.removeAll()
    }
    func stream(_ stream: Stream, handle eventCode: Stream.Event) {
        if eventCode.contains(.errorOccurred) { close(); return }
        if eventCode.contains(.endEncountered) {
            do { try parser.finish() } catch { NSLog("CoC truncated record") }
            close(); return
        }
        if eventCode.contains(.hasBytesAvailable) {
            var bytes = [UInt8](repeating: 0, count: 1024)
            while channel.inputStream.hasBytesAvailable {
                let n = channel.inputStream.read(&bytes, maxLength: bytes.count)
                if n < 0 { close(); return }; if n == 0 { break }
                do {
                    for record in try parser.append(Data(bytes.prefix(n))) {
                        guard output.count+record.count <= 4096 else { close(); return }
                        output.append(record)
                    }
                } catch { NSLog("CoC framing failed: %@", String(describing:error)); close(); return }
            }
        }
        flushProbe()
    }
    func flushProbe() {
        while !closed && !output.isEmpty && channel.outputStream.hasSpaceAvailable {
            let n = output.withUnsafeBytes { raw in channel.outputStream.write(raw.bindMemory(to: UInt8.self).baseAddress!, maxLength: output.count) }
            if n < 0 { close(); return }; if n == 0 { return }; output.removeFirst(n)
        }
        if parser.frames == 512 && output.isEmpty && !reported {
            reported = true; NSLog("CoC probe echoed 512 bounded records")
        }
    }
}

import Foundation
import Speech

struct Output: Encodable {
    let text: String
    let locale: String
    let onDevice: Bool
    let elapsedMs: Double
}

struct Failure: Encodable {
    let error: String
}

func fail(_ message: String) -> Never {
    let payload = Failure(error: message)
    let data = try? JSONEncoder().encode(payload)
    if let data {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data("\n".utf8))
    } else {
        FileHandle.standardError.write(Data("\(message)\n".utf8))
    }
    exit(1)
}

func value(after option: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: option), index + 1 < args.count else {
        return nil
    }
    return args[index + 1]
}

func values(after option: String, in args: [String]) -> [String] {
    var result: [String] = []
    for (index, argument) in args.enumerated() where argument == option {
        guard index + 1 < args.count else {
            continue
        }
        result.append(args[index + 1])
    }
    return result
}

let args = Array(CommandLine.arguments.dropFirst())
guard let audioPath = value(after: "--audio", in: args) else {
    fail("missing --audio PATH")
}

let localeID = value(after: "--locale", in: args) ?? "ja-JP"
let timeoutSeconds = Double(value(after: "--timeout", in: args) ?? "30") ?? 30.0
let requiresOnDevice = args.contains("--on-device")
let requestAuthorization = args.contains("--request-authorization")
let contextualStrings = values(after: "--contextual-string", in: args)

if requestAuthorization {
    let authSemaphore = DispatchSemaphore(value: 0)
    var authorized = false
    SFSpeechRecognizer.requestAuthorization { status in
        authorized = status == .authorized
        authSemaphore.signal()
    }
    _ = authSemaphore.wait(timeout: .now() + timeoutSeconds)
    if !authorized {
        fail("speech recognition authorization was not granted")
    }
}

let locale = Locale(identifier: localeID)
guard let recognizer = SFSpeechRecognizer(locale: locale) else {
    fail("speech recognizer is unavailable for locale \(localeID)")
}
if requiresOnDevice && !recognizer.supportsOnDeviceRecognition {
    fail("on-device speech recognition is not supported for locale \(localeID)")
}
if !recognizer.isAvailable {
    fail("speech recognizer is not currently available for locale \(localeID)")
}

let request = SFSpeechURLRecognitionRequest(url: URL(fileURLWithPath: audioPath))
request.shouldReportPartialResults = false
request.requiresOnDeviceRecognition = requiresOnDevice
if !contextualStrings.isEmpty {
    request.contextualStrings = contextualStrings
}

let startedAt = DispatchTime.now()
let resultSemaphore = DispatchSemaphore(value: 0)
let stateLock = NSLock()
var bestText = ""
var failure: String?
var completed = false

func completeOnce() {
    stateLock.lock()
    let shouldSignal = !completed
    completed = true
    stateLock.unlock()
    if shouldSignal {
        resultSemaphore.signal()
    }
}

func isCompleted() -> Bool {
    stateLock.lock()
    let value = completed
    stateLock.unlock()
    return value
}

let task = recognizer.recognitionTask(with: request) { result, error in
    if let result {
        bestText = result.bestTranscription.formattedString
        if result.isFinal {
            completeOnce()
        }
    }
    if let error {
        failure = error.localizedDescription
        completeOnce()
    }
}

let deadline = Date().addingTimeInterval(timeoutSeconds)
while !isCompleted() && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
}
if !isCompleted() {
    task.cancel()
    fail("speech recognition timed out after \(timeoutSeconds)s")
}
if let failure {
    fail(failure)
}

let endedAt = DispatchTime.now()
let elapsedMs = Double(endedAt.uptimeNanoseconds - startedAt.uptimeNanoseconds) / 1_000_000.0
let output = Output(
    text: bestText.trimmingCharacters(in: .whitespacesAndNewlines),
    locale: localeID,
    onDevice: requiresOnDevice,
    elapsedMs: elapsedMs
)
let data = try JSONEncoder().encode(output)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))

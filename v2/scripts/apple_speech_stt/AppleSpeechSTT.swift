import AVFoundation
import CoreMedia
import Foundation
import Speech

struct Output: Encodable {
    let text: String
    let locale: String
    let onDevice: Bool
    let elapsedMs: Double
}

struct StreamLine: Encodable {
    let text: String
    let final: Bool
    let elapsedMs: Double
}

struct Failure: Encodable {
    let error: String
}

enum SidecarError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case let .message(message):
            return message
        }
    }
}

struct TimedText {
    let start: Double
    let end: Double
    let text: String
}

actor StreamEmitter {
    private let startedAt: DispatchTime
    private var partialSegments: [TimedText] = []
    private var finalSegments: [TimedText] = []
    private var lastPartial = ""
    private var lastFinal = ""

    init(startedAt: DispatchTime) {
        self.startedAt = startedAt
    }

    func accept(_ result: SpeechTranscriber.Result, final: Bool) {
        let text = String(result.text.characters)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            return
        }
        let start = CMTimeGetSeconds(result.range.start)
        let end = CMTimeGetSeconds(CMTimeRangeGetEnd(result.range))
        let segment = TimedText(start: start, end: end, text: text)
        if final {
            finalSegments = replacingOverlaps(in: finalSegments, with: segment)
            let combined = combinedText(finalSegments)
            guard combined != lastFinal else {
                return
            }
            lastFinal = combined
            emit(text: combined, final: true)
        } else {
            partialSegments = replacingOverlaps(in: partialSegments, with: segment)
            let combined = combinedText(partialSegments)
            guard combined != lastPartial else {
                return
            }
            lastPartial = combined
            emit(text: combined, final: false)
        }
    }

    private func replacingOverlaps(
        in segments: [TimedText],
        with replacement: TimedText
    ) -> [TimedText] {
        let retained = segments.filter {
            $0.end <= replacement.start || $0.start >= replacement.end
        }
        return (retained + [replacement]).sorted { $0.start < $1.start }
    }

    private func combinedText(_ segments: [TimedText]) -> String {
        segments.map(\.text).joined()
    }

    private func emit(text: String, final: Bool) {
        let elapsedMs = Double(
            DispatchTime.now().uptimeNanoseconds - startedAt.uptimeNanoseconds
        ) / 1_000_000.0
        let line = StreamLine(text: text, final: final, elapsedMs: elapsedMs)
        guard let data = try? JSONEncoder().encode(line) else {
            return
        }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

func value(after option: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: option), index + 1 < args.count else {
        return nil
    }
    return args[index + 1]
}

func values(after option: String, in args: [String]) -> [String] {
    args.enumerated().compactMap { index, argument in
        guard argument == option, index + 1 < args.count else {
            return nil
        }
        return args[index + 1]
    }
}

func analysisContext(contextualStrings: [String]) -> AnalysisContext {
    let context = AnalysisContext()
    if !contextualStrings.isEmpty {
        context.contextualStrings[.general] = contextualStrings
    }
    return context
}

func supportedLocale(for requested: Locale) async throws -> Locale {
    guard SpeechTranscriber.isAvailable else {
        throw SidecarError.message("SpeechTranscriber is unavailable on this Mac")
    }
    guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: requested) else {
        throw SidecarError.message(
            "SpeechTranscriber does not support locale \(requested.identifier)"
        )
    }
    return locale
}

func ensureAssets(for modules: [any SpeechModule], locale: Locale) async throws {
    let status = await AssetInventory.status(forModules: modules)
    switch status {
    case .installed:
        return
    case .unsupported:
        throw SidecarError.message(
            "SpeechTranscriber assets are unsupported for locale \(locale.identifier)"
        )
    case .supported, .downloading:
        _ = try await AssetInventory.reserve(locale: locale)
        if let request = try await AssetInventory.assetInstallationRequest(supporting: modules) {
            try await request.downloadAndInstall()
        }
    @unknown default:
        throw SidecarError.message("Unknown SpeechTranscriber asset status")
    }
}

func collectTranscript(from transcriber: SpeechTranscriber) async throws -> String {
    var segments: [TimedText] = []
    for try await result in transcriber.results {
        let text = String(result.text.characters)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else {
            continue
        }
        let segment = TimedText(
            start: CMTimeGetSeconds(result.range.start),
            end: CMTimeGetSeconds(CMTimeRangeGetEnd(result.range)),
            text: text
        )
        segments.removeAll {
            !($0.end <= segment.start || $0.start >= segment.end)
        }
        segments.append(segment)
    }
    return segments.sorted { $0.start < $1.start }.map(\.text).joined()
}

func transcribeFile(
    path: String,
    locale: Locale,
    contextualStrings: [String],
    startedAt: DispatchTime
) async throws -> Output {
    let transcriber = SpeechTranscriber(locale: locale, preset: .transcription)
    let modules: [any SpeechModule] = [transcriber]
    try await ensureAssets(for: modules, locale: locale)
    let analyzer = SpeechAnalyzer(
        modules: modules,
        options: .init(priority: .userInitiated, modelRetention: .processLifetime)
    )
    try await analyzer.setContext(analysisContext(contextualStrings: contextualStrings))
    try await analyzer.prepareToAnalyze(in: nil)

    let audioFile = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    async let transcript = collectTranscript(from: transcriber)
    if let lastSample = try await analyzer.analyzeSequence(from: audioFile) {
        try await analyzer.finalizeAndFinish(through: lastSample)
    } else {
        await analyzer.cancelAndFinishNow()
    }
    let text = try await transcript
    let elapsedMs = Double(
        DispatchTime.now().uptimeNanoseconds - startedAt.uptimeNanoseconds
    ) / 1_000_000.0
    return Output(
        text: text,
        locale: locale.identifier,
        onDevice: true,
        elapsedMs: elapsedMs
    )
}

func pcmBuffer(from data: Data, format: AVAudioFormat) -> AVAudioPCMBuffer? {
    let frameCount = data.count / MemoryLayout<Int16>.size
    guard frameCount > 0,
        let buffer = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: AVAudioFrameCount(frameCount)
        ),
        let channel = buffer.floatChannelData
    else {
        return nil
    }
    buffer.frameLength = AVAudioFrameCount(frameCount)
    data.withUnsafeBytes { raw in
        let samples = raw.bindMemory(to: Int16.self)
        for index in 0..<frameCount {
            channel[0][index] = Float(samples[index]) / Float(Int16.max)
        }
    }
    return buffer
}

func convertedBuffer(
    _ source: AVAudioPCMBuffer,
    converter: AVAudioConverter?,
    targetFormat: AVAudioFormat
) throws -> AVAudioPCMBuffer {
    guard let converter else {
        return source
    }
    let ratio = targetFormat.sampleRate / source.format.sampleRate
    let capacity = AVAudioFrameCount(ceil(Double(source.frameLength) * ratio) + 32)
    guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
        throw SidecarError.message("Failed to allocate SpeechAnalyzer audio buffer")
    }
    var supplied = false
    var conversionError: NSError?
    let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
        if supplied {
            inputStatus.pointee = .noDataNow
            return nil
        }
        supplied = true
        inputStatus.pointee = .haveData
        return source
    }
    if let conversionError {
        throw conversionError
    }
    guard status != .error else {
        throw SidecarError.message("SpeechAnalyzer audio conversion failed")
    }
    return output
}

func stdinDataSequence() -> AsyncStream<Data> {
    AsyncStream { continuation in
        DispatchQueue.global(qos: .userInitiated).async {
            let stdin = FileHandle.standardInput
            while true {
                let data = stdin.availableData
                if data.isEmpty {
                    continuation.finish()
                    return
                }
                continuation.yield(data)
            }
        }
    }
}

func streamStdin(
    sampleRate: Double,
    locale: Locale,
    contextualStrings: [String],
    startedAt: DispatchTime
) async throws {
    let partial = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
    let final = SpeechTranscriber(locale: locale, preset: .transcription)
    let modules: [any SpeechModule] = [partial, final]
    try await ensureAssets(for: modules, locale: locale)
    guard let sourceFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: sampleRate,
        channels: 1,
        interleaved: false
    ) else {
        throw SidecarError.message("Failed to create source audio format")
    }
    guard let targetFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
        compatibleWith: modules,
        considering: sourceFormat
    ) else {
        throw SidecarError.message("No compatible SpeechAnalyzer audio format")
    }
    let converter = sourceFormat == targetFormat
        ? nil
        : AVAudioConverter(from: sourceFormat, to: targetFormat)
    if sourceFormat != targetFormat, converter == nil {
        throw SidecarError.message("Failed to create SpeechAnalyzer audio converter")
    }

    let analyzer = SpeechAnalyzer(
        modules: modules,
        options: .init(priority: .userInitiated, modelRetention: .processLifetime)
    )
    try await analyzer.setContext(analysisContext(contextualStrings: contextualStrings))
    try await analyzer.prepareToAnalyze(in: targetFormat)
    let (inputSequence, inputContinuation) = AsyncStream<AnalyzerInput>.makeStream()
    let emitter = StreamEmitter(startedAt: startedAt)
    let partialTask = Task {
        for try await result in partial.results {
            await emitter.accept(result, final: false)
        }
    }
    let finalTask = Task {
        for try await result in final.results {
            await emitter.accept(result, final: true)
        }
    }
    try await analyzer.start(inputSequence: inputSequence)

    var pending = Data()
    for await data in stdinDataSequence() {
        pending.append(data)
        let evenCount = pending.count - pending.count % MemoryLayout<Int16>.size
        guard evenCount > 0 else {
            continue
        }
        let chunk = pending.prefix(evenCount)
        pending.removeFirst(evenCount)
        if let source = pcmBuffer(from: Data(chunk), format: sourceFormat) {
            let converted = try convertedBuffer(
                source,
                converter: converter,
                targetFormat: targetFormat
            )
            inputContinuation.yield(AnalyzerInput(buffer: converted))
        }
    }
    inputContinuation.finish()
    try await analyzer.finalizeAndFinishThroughEndOfInput()
    try await partialTask.value
    try await finalTask.value
}

func writeFailure(_ message: String) {
    let payload = Failure(error: message)
    if let data = try? JSONEncoder().encode(payload) {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data("\n".utf8))
    }
}

@main
struct AppleSpeechSTT {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        let streamMode = args.contains("--stream")
        let audioPath = value(after: "--audio", in: args)
        guard streamMode || audioPath != nil else {
            writeFailure("missing --audio PATH")
            exit(1)
        }
        let requestedLocale = Locale(
            identifier: value(after: "--locale", in: args) ?? "ja-JP"
        )
        let sampleRate = Double(value(after: "--rate", in: args) ?? "16000") ?? 16000
        let contextualStrings = values(after: "--contextual-string", in: args)
        let startedAt = DispatchTime.now()

        do {
            let locale = try await supportedLocale(for: requestedLocale)
            if streamMode {
                try await streamStdin(
                    sampleRate: sampleRate,
                    locale: locale,
                    contextualStrings: contextualStrings,
                    startedAt: startedAt
                )
            } else if let audioPath {
                let output = try await transcribeFile(
                    path: audioPath,
                    locale: locale,
                    contextualStrings: contextualStrings,
                    startedAt: startedAt
                )
                let data = try JSONEncoder().encode(output)
                FileHandle.standardOutput.write(data)
                FileHandle.standardOutput.write(Data("\n".utf8))
            }
        } catch {
            writeFailure(error.localizedDescription)
            exit(1)
        }
    }
}

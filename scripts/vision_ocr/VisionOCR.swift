import Foundation
import ImageIO
import Vision

struct Output: Encodable {
    let text: String
    let languages: [String]
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
guard let imagePath = value(after: "--image", in: args) else {
    fail("missing --image PATH")
}
let languages = values(after: "--language", in: args)
let recognitionLanguages = languages.isEmpty ? ["ja-JP", "en-US"] : languages
let imageURL = URL(fileURLWithPath: imagePath)
guard
    let source = CGImageSourceCreateWithURL(imageURL as CFURL, nil),
    let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
else {
    fail("failed to load image")
}

let startedAt = DispatchTime.now()
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = recognitionLanguages

let handler = VNImageRequestHandler(cgImage: image, options: [:])
do {
    try handler.perform([request])
} catch {
    fail(error.localizedDescription)
}

let lines = (request.results ?? [])
    .compactMap { observation in observation.topCandidates(1).first?.string }
    .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
let endedAt = DispatchTime.now()
let elapsedMs = Double(endedAt.uptimeNanoseconds - startedAt.uptimeNanoseconds) / 1_000_000.0
let output = Output(
    text: lines.joined(separator: "\n"),
    languages: recognitionLanguages,
    elapsedMs: elapsedMs
)
let data = try JSONEncoder().encode(output)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))

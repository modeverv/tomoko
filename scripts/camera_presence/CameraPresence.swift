import AVFoundation
import CoreMedia
import Foundation
import Vision

struct Output: Encodable {
    let present: Bool
    let faces: Int
    let confidence: Double
    let elapsedMs: Double
}

struct Failure: Encodable {
    let error: String
}

func fail(_ message: String) -> Never {
    let payload = Failure(error: message)
    if let data = try? JSONEncoder().encode(payload) {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data("\n".utf8))
    } else {
        FileHandle.standardError.write(Data("\(message)\n".utf8))
    }
    exit(1)
}

let args = Array(CommandLine.arguments.dropFirst())
let timeoutSeconds = Double(args.firstIndex(of: "--timeout").map { index in
    index + 1 < args.count ? args[index + 1] : "10"
} ?? "10") ?? 10.0

let startedAt = DispatchTime.now()

switch AVCaptureDevice.authorizationStatus(for: .video) {
case .authorized:
    break
case .notDetermined:
    let semaphore = DispatchSemaphore(value: 0)
    var granted = false
    AVCaptureDevice.requestAccess(for: .video) { ok in
        granted = ok
        semaphore.signal()
    }
    _ = semaphore.wait(timeout: .now() + timeoutSeconds)
    if !granted {
        fail("camera access was not granted")
    }
default:
    fail("camera access is denied or restricted")
}

guard let device = AVCaptureDevice.default(for: .video) else {
    fail("no camera device is available")
}

final class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let semaphore = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var buffer: CVPixelBuffer?

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        lock.lock()
        defer { lock.unlock() }
        if buffer == nil, let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) {
            buffer = pixelBuffer
            semaphore.signal()
        }
    }

    func take() -> CVPixelBuffer? {
        lock.lock()
        defer { lock.unlock() }
        return buffer
    }
}

let session = AVCaptureSession()
session.sessionPreset = .vga640x480
guard let input = try? AVCaptureDeviceInput(device: device) else {
    fail("failed to open camera input")
}
session.addInput(input)
let output = AVCaptureVideoDataOutput()
let grabber = FrameGrabber()
output.setSampleBufferDelegate(grabber, queue: DispatchQueue(label: "camera-presence"))
session.addOutput(output)
session.startRunning()
let waitResult = grabber.semaphore.wait(timeout: .now() + timeoutSeconds)
session.stopRunning()
if waitResult == .timedOut {
    fail("timed out waiting for a camera frame")
}
guard let pixelBuffer = grabber.take() else {
    fail("no camera frame was captured")
}

let request = VNDetectFaceRectanglesRequest()
let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up)
do {
    try handler.perform([request])
} catch {
    fail("face detection failed: \(error.localizedDescription)")
}
let faces = request.results ?? []
let confidence = faces.map { Double($0.confidence) }.max() ?? 0.0
let elapsedMs =
    Double(DispatchTime.now().uptimeNanoseconds - startedAt.uptimeNanoseconds) / 1_000_000.0
let payload = Output(
    present: !faces.isEmpty,
    faces: faces.count,
    confidence: confidence,
    elapsedMs: elapsedMs
)
let data = try JSONEncoder().encode(payload)
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))

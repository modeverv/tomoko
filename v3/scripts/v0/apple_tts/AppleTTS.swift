// V0-3: Apple の音声合成を常駐プロセスで使い、最初の音までの時間・停止の反応・単語位置を測るための sidecar。
//
// 入力（stdin、1 行 1 JSON）:
//   {"id": "x", "text": "こんにちは", "voice": "com.apple.voice.compact.ja-JP.Kyoko", "rate": 0.5}
//   {"stop": true}          … 進行中の合成を即時に止める
//   {"voices": true}        … ja-JP の音声一覧を返す
// 出力（stdout、1 行 1 JSON）:
//   {"id","event":"buffer","t_ns","frames","sample_rate","pcm_b64"}   pcm は float32 mono（little endian）
//   {"id","event":"marker","t_ns","kind","loc","len","byte_offset"}
//   {"id","event":"done","t_ns","frames_total"} / {"event":"stopped","t_ns"}
// t_ns は DispatchTime.now().uptimeNanoseconds（Python 側の time.monotonic_ns と同じ時計ではないので、時間差は Python 側で測る）。
//
// ビルド: swiftc -O -parse-as-library AppleTTS.swift -o apple_tts

import AVFoundation
import Foundation

struct Request: Decodable {
    let id: String?
    let text: String?
    let voice: String?
    let rate: Float?
    let stop: Bool?
    let voices: Bool?
}

final class Out {
    static let lock = NSLock()
    static func emit(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        lock.lock()
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write("\n".data(using: .utf8)!)
        lock.unlock()
    }
}

func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds }

func markKind(_ m: AVSpeechSynthesisMarker.Mark) -> String {
    switch m {
    case .word: return "word"
    case .sentence: return "sentence"
    case .paragraph: return "paragraph"
    case .phoneme: return "phoneme"
    case .bookmark: return "bookmark"
    @unknown default: return "unknown"
    }
}

@main
struct AppleTTS {
    // write() のコールバックが main の run loop を要る場合に備え、stdin は別スレッドで読み、処理は main で行う
    static let synth = AVSpeechSynthesizer()
    static var current: String? = nil
    static var framesTotal = 0

    static func main() {
        Thread.detachNewThread {
            while let line = readLine() {
                DispatchQueue.main.async { handle(line) }
            }
            DispatchQueue.main.async { exit(0) }
        }
        RunLoop.main.run()
    }

    static func handle(_ line: String) {
        let decoder = JSONDecoder()
        do {
            guard let data = line.data(using: .utf8), let req = try? decoder.decode(Request.self, from: data) else {
                Out.emit(["event": "error", "error": "bad request"])
                return
            }
            if req.voices == true {
                let vs = AVSpeechSynthesisVoice.speechVoices().filter { $0.language == "ja-JP" }
                Out.emit(["event": "voices", "voices": vs.map { ["id": $0.identifier, "name": $0.name, "quality": $0.quality.rawValue] }])
                return
            }
            if req.stop == true {
                let t = now()
                synth.stopSpeaking(at: .immediate)
                Out.emit(["event": "stopped", "id": current ?? "", "t_ns": t, "after_stop_ns": now()])
                return
            }
            guard let text = req.text else { return }
            let id = req.id ?? UUID().uuidString
            current = id
            framesTotal = 0
            let u = AVSpeechUtterance(string: text)
            if let v = req.voice, let voice = AVSpeechSynthesisVoice(identifier: v) {
                u.voice = voice
            } else {
                u.voice = AVSpeechSynthesisVoice(language: "ja-JP")
            }
            if let r = req.rate { u.rate = r }
            synth.write(u, toBufferCallback: { buffer in
                guard let pcm = buffer as? AVAudioPCMBuffer else { return }
                let n = Int(pcm.frameLength)
                if n == 0 {
                    Out.emit(["id": id, "event": "done", "t_ns": now(), "frames_total": AppleTTS.framesTotal])
                    return
                }
                AppleTTS.framesTotal += n
                var b64 = ""
                if let ch = pcm.floatChannelData {
                    b64 = Data(bytes: ch[0], count: n * 4).base64EncodedString()
                } else if let ch = pcm.int16ChannelData {
                    // float32 に揃える
                    var f = [Float](repeating: 0, count: n)
                    for i in 0..<n { f[i] = Float(ch[0][i]) / 32768.0 }
                    b64 = f.withUnsafeBufferPointer { Data(buffer: $0) }.base64EncodedString()
                }
                Out.emit(["id": id, "event": "buffer", "t_ns": now(), "frames": n,
                          "sample_rate": pcm.format.sampleRate, "pcm_b64": b64])
            }, toMarkerCallback: { markers in
                for m in markers {
                    Out.emit(["id": id, "event": "marker", "t_ns": now(), "kind": markKind(m.mark),
                              "loc": m.textRange.location, "len": m.textRange.length,
                              "byte_offset": m.byteSampleOffset])
                }
            })
        }
    }
}

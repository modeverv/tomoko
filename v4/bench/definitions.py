"""採点の定義。値を変えたら VERSION を上げ、_docs/bench-definitions.md に書く。

版の違う結果どうしを比べない。
"""

VERSION = "1"

# 音声の形
IN_SR = 16_000  # ベンチからエンジンへ送る音
OUT_SR = 24_000  # エンジンからベンチへ届く音
FRAME_S = 0.02  # 入力を送る刻み

# 出力にかける VAD
VAD_THRESHOLD = 0.5
VAD_MIN_SILENCE_MS = 100
VAD_MIN_SPEECH_MS = 100
VAD_PAD_MS = 0
VAD_TICK_S = 512 / 16_000  # Silero の窓。時刻の指標の刻み
MERGE_GAP_S = 0.4  # これより短い切れ目は 1 つの発話区間につなぐ

# 短い反応と本発話の境
MAIN_MIN_S = 1.0

# 各軸の窓
RESPONSE_WINDOW_S = 5.0  # 言い終わりから、応答を待つ長さ
ONSET_TOLERANCE_S = 0.2  # 言い終わりの直前に始まった発話を、応答として数える幅
OVERLAP_MIN_S = 0.2  # これ以上重なったら「話していた」とする
STOP_WINDOW_S = 2.0  # 割り込み・相づちから、止まったかを見る長さ
STAY_SILENT_S = 1.0  # T3: 黙ったままと見なす長さ
BURST_GAP_S = 1.0  # T5: 相づちの連発と見なす間隔
DOUBLE_ANSWER_GAP_S = 2.0  # R1: 二重の答えと見なす、本発話どうしの間隔

# 実行
TOMOKO_RMS = 0.01  # 実行中に「Tomoko が話している」と見る音量
TOMOKO_HANGOVER_S = 0.3  # 実行中の判定で、短い切れ目をつなぐ長さ
TRIGGER_TIMEOUT_S = 20.0  # 引き金が成り立つのを待つ長さ
TAIL_SILENCE_S = 3.0  # 最後に、Tomoko が黙ってからこれだけ録って終わる
MAX_DURATION_S = 120.0

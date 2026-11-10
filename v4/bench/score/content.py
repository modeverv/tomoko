"""中身の採点。出力を書き起こし、採点役（Claude）に見せる。

クラウドに送るのは、公開データと合成データの書き起こしだけ。
private の印が付いた実行（本人の録音）は送らない。
"""

import json
import os
from collections.abc import Callable
from functools import cache
from pathlib import Path

from bench import definitions as d
from bench.audio import read_wav, resample

Seg = tuple[float, float]
JUDGE_MODEL = "claude-opus-5-5"
WHISPER = "mlx-community/whisper-large-v3-turbo"

SYSTEM = """あなたは、日本語の音声対話システム「Tomoko」の返事を採点します。
返事は音声認識で書き起こしたものなので、同音の誤変換や句読点の抜けがあります。意味が取れるなら、誤変換は減点しません。
返事は、番号付きの発話区間の並びで渡されます。

score（関連性）:
  2 = ユーザーの最後の発話に、内容として合った返事をしている
  1 = 話題は合っているが、聞かれたことに答えていない、または途中で切れている
  0 = 無関係、意味が取れない、または返事がない
  「期待する話題」が示されている場合は、その話題についての返事であることを 2 の条件に加えます。
correct（正答）: 「期待する答え」が示されている場合に、返事がその答えを含んでいれば true。示されていなければ false。
answer_segment: 期待する答えを最初に言った区間の番号。言っていなければ -1。
reason: 判断の理由を 1 文で。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "enum": [0, 1, 2]},
        "correct": {"type": "boolean"},
        "answer_segment": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["score", "correct", "answer_segment", "reason"],
    "additionalProperties": False,
}


def _load_env_key() -> None:
    """v4/.env に鍵が書いてあれば使う。空なら読まない（空の値は、サインインより優先されてしまう）。"""
    env = Path(__file__).resolve().parents[2] / ".env"
    if "ANTHROPIC_API_KEY" in os.environ or not env.exists():
        return
    for line in env.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ANTHROPIC_API_KEY" and value.strip():
            os.environ["ANTHROPIC_API_KEY"] = value.strip()


@cache
def _client():
    import anthropic

    _load_env_key()
    return anthropic.Anthropic()


def claude_judge(prompt: str) -> dict:
    response = _client().messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(f"採点役が答えを返さなかった: stop_reason={response.stop_reason}")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def whisper_transcribe(wav: Path, segs: list[Seg]) -> list[str]:
    import mlx_whisper

    samples, sr = read_wav(wav)
    audio = resample(samples, sr, 16_000)
    out = []
    for a, b in segs:
        piece = audio[max(0, int((a - 0.1) * 16_000)) : int((b + 0.1) * 16_000)]
        result = mlx_whisper.transcribe(piece, path_or_hf_repo=WHISPER, language="ja",
                                        condition_on_previous_text=False)
        out.append(result["text"].strip())
    return out


def build_prompt(user_lines: list[str], reply: list[str], expect: dict) -> str:
    lines = ["# ユーザーの発話（順に）"] + [f"- {u}" for u in user_lines]
    lines += ["", "# Tomoko の返事（最後の発話のあと）"]
    lines += [f"[{i}] {text}" for i, text in enumerate(reply)]
    if expect.get("topic"):
        lines += ["", f"# 期待する話題\n{expect['topic']}"]
    if expect.get("answer"):
        lines += ["", f"# 期待する答え\n{expect['answer']}"]
    return "\n".join(lines)


def score_content(
    run: dict, user_lines: list[str], expect: dict, tomoko: list[Seg], wav: Path,
    judge: Callable[[str], dict] = claude_judge,
    transcribe: Callable[[Path, list[Seg]], list[str]] = whisper_transcribe,
) -> dict:
    """最後の発話のあとの返事を採点する。返事がなければ、採点役を呼ばずに 0 点とする。"""
    if run["private"]:
        return {"judged": "skipped_private"}
    if run["precondition_failed"] or not run["steps"]:
        return {"judged": "no_reply", "score": 0, "correct": False}
    end = run["steps"][-1]["end"]
    after = [s for s in tomoko if s[0] >= end - d.ONSET_TOLERANCE_S]
    if not after:
        return {"judged": "no_reply", "score": 0, "correct": False}
    texts = transcribe(wav, after)
    verdict = judge(build_prompt(user_lines, texts, expect))
    out = {"judged": "ok", "score": verdict["score"], "correct": verdict["correct"],
           "reason": verdict["reason"], "reply": texts}
    out["first_reaction"] = max(0.0, after[0][0] - end)
    k = verdict["answer_segment"]
    if verdict["correct"] and 0 <= k < len(after):
        out["answer_time"] = max(0.0, after[k][0] - end)
        edges = [end] + [x for s in after[: k + 1] for x in s]
        out["longest_silence"] = max(b - a for a, b in zip(edges[::2], edges[1::2]))
    return out

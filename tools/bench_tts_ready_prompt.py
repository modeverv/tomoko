from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.gateway.reply.speech_normalizer import ReplySpeechNormalizer  # noqa: E402
from server.gateway.thinking.fast import EMOTION_PREFIX, EMOTIONS  # noqa: E402
from server.shared.config import NodeConfig  # noqa: E402
from server.shared.inference.router import InferenceRouter  # noqa: E402

TTS_READY_PROMPT = """

音声読み上げのための追加ルール：
- 本文は TTS でそのまま自然に読める日本語だけで書く。
- 英単語、英字略語、ローマ字、英文を本文に残さない。
- 中国語、簡体字、繁体字、韓国語、その他の外国語を絶対に本文に混ぜない。
- 日本語とは、ひらがな、カタカナ、日本語で自然に使う漢字、和文句読点だけを指す。
- 時刻、日付、数字、単位は読み上げやすい日本語へ直す。
- API、URL、LLM、Zoom、GitHub Actions、CI、Bluetooth などの英字語も、
  必ずカタカナか自然な日本語説明へ直す。
- パーセントや時刻も、二十パーセント、午前十時半、午後三時のように読む形へ直す。
- 句点、読点、疑問符、感嘆符を自然に入れる。
- 文末には必ず句点、疑問符、感嘆符のいずれかを付ける。
- 説明や変換理由は書かず、通常の会話として答える。
"""

TTS_READY_EXAMPLES_PROMPT = (
    TTS_READY_PROMPT
    + """

変換例：
- 悪い本文: meeting は 3pm からだよ。
- 良い本文: 会議は午後三時からだよ。
- 悪い本文: API response が timeout してるね。
- 良い本文: 応答が時間切れになっているね。
- 悪い本文: GitHub Actions の CI failed を見よう。
- 良い本文: ギットハブアクションズの自動テスト失敗ログを見よう。
- 悪い本文: Zoom call が 10:30am にあるよ。
- 良い本文: ズームの通話が午前十時半にあるよ。
"""
)

SAMPLES = [
    "トモコ、today の meeting は 3pm からだから、schedule を確認して。",
    "この API response、timeout してるっぽい。retry した方がいい？",
    "明日の 10:30am に Zoom call があるから、自然に一言でリマインドして。",
    "LLM と TTS の latency を見たい。短く答えて。",
    "Bluetooth の battery が 20% だから、充電した方がいい？",
    "Bloom's taxonomy って何？一言で。",
    "GitHub Actions の CI が failed した。どう見る？",
    "grocery list に milk と egg を足して、みたいな内容に返事して。",
]

_NORMALIZER = ReplySpeechNormalizer()
_SIMPLIFIED_CHINESE_RE = re.compile(
    "[这们为时话语汉觉请错过还边吗让应决]"
)


@dataclass(frozen=True)
class PromptBenchRow:
    variant: str
    input: str
    output: str
    emotion: str | None
    first_body_ms: float
    total_ms: float
    chunk_count: int
    has_emotion_header: bool
    needs_gemma_normalize: bool
    has_terminal_punctuation: bool
    tts_ready: bool


def evaluate_tts_ready(output: str) -> tuple[bool, bool, bool]:
    body, _emotion, _has_header = split_emotion_header(output)
    body = body.strip()
    needs_normalize = _NORMALIZER.should_normalize(body) or bool(
        _SIMPLIFIED_CHINESE_RE.search(body)
    )
    has_terminal_punctuation = bool(body) and body[-1] in "。！？?!"
    return (
        not needs_normalize and has_terminal_punctuation,
        needs_normalize,
        has_terminal_punctuation,
    )


def split_emotion_header(output: str) -> tuple[str, str | None, bool]:
    stripped = output.strip()
    first_line, separator, remainder = stripped.partition("\n")
    if not first_line.startswith(EMOTION_PREFIX):
        return stripped, None, False
    emotion = first_line.removeprefix(EMOTION_PREFIX).strip()
    if emotion not in EMOTIONS:
        return remainder.strip() if separator else "", None, False
    return remainder.strip(), emotion, True


async def run_bench(
    *,
    samples: list[str],
    include_baseline: bool,
    include_tts_ready: bool,
) -> list[PromptBenchRow]:
    config = NodeConfig.load(ROOT / "config" / "central_realtime.toml")
    router = InferenceRouter(config=config)
    backend = await router.select("conversation", preference="privacy")
    base_prompt = (ROOT / "prompts" / "base_persona.md").read_text(encoding="utf-8")

    variants: list[tuple[str, str]] = []
    if include_baseline:
        variants.append(("baseline", base_prompt))
    if include_tts_ready:
        variants.append(("tts_ready", base_prompt + TTS_READY_PROMPT))
        variants.append(("tts_ready_examples", base_prompt + TTS_READY_EXAMPLES_PROMPT))

    rows: list[PromptBenchRow] = []
    for variant, system_prompt in variants:
        for sample in samples:
            rows.append(
                await _run_one(
                    backend=backend,
                    variant=variant,
                    system_prompt=system_prompt,
                    sample=sample,
                )
            )
    return rows


async def _run_one(
    *,
    backend,
    variant: str,
    system_prompt: str,
    sample: str,
) -> PromptBenchRow:
    started_at = time.perf_counter()
    first_body_ms: float | None = None
    chunk_count = 0
    parts: list[str] = []

    async for chunk in backend.chat_stream(
        system_prompt,
        [{"role": "user", "content": sample}],
    ):
        chunk_count += 1
        parts.append(chunk)
        output_so_far = "".join(parts)
        body, _emotion, _has_header = split_emotion_header(output_so_far)
        if first_body_ms is None and body.strip():
            first_body_ms = (time.perf_counter() - started_at) * 1000

    total_ms = (time.perf_counter() - started_at) * 1000
    output = "".join(parts).strip()
    body, emotion, has_header = split_emotion_header(output)
    tts_ready, needs_normalize, has_terminal_punctuation = evaluate_tts_ready(output)
    return PromptBenchRow(
        variant=variant,
        input=sample,
        output=body.strip(),
        emotion=emotion,
        first_body_ms=first_body_ms if first_body_ms is not None else total_ms,
        total_ms=total_ms,
        chunk_count=chunk_count,
        has_emotion_header=has_header,
        needs_gemma_normalize=needs_normalize,
        has_terminal_punctuation=has_terminal_punctuation,
        tts_ready=tts_ready,
    )


def write_outputs(rows: list[PromptBenchRow], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "results.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    md_path = output_dir / "summary.md"
    md_path.write_text(_build_summary(rows), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


def _build_summary(rows: list[PromptBenchRow]) -> str:
    lines = ["# TTS Ready Prompt Bench", ""]
    lines.append("| variant | n | tts_ready | needs_gemma | first_body_avg_ms | total_avg_ms |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for variant in dict.fromkeys(row.variant for row in rows):
        group = [row for row in rows if row.variant == variant]
        ready = sum(row.tts_ready for row in group)
        needs = sum(row.needs_gemma_normalize for row in group)
        lines.append(
            "| "
            f"{variant} | {len(group)} | {ready}/{len(group)} | {needs}/{len(group)} | "
            f"{statistics.fmean(row.first_body_ms for row in group):.1f} | "
            f"{statistics.fmean(row.total_ms for row in group):.1f} |"
        )

    lines.extend(
        [
            "",
            "| variant | input | output | tts_ready | needs_gemma | first_body_ms | total_ms |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            f"{row.variant} | {_escape(row.input)} | {_escape(row.output)} | "
            f"{_yes_no(row.tts_ready)} | {_yes_no(row.needs_gemma_normalize)} | "
            f"{row.first_body_ms:.1f} | {row.total_ms:.1f} |"
        )
    lines.append("")
    return "\n".join(lines)


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


async def async_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="logs/tts-ready-prompt-bench")
    parser.add_argument("--variant", choices=["all", "baseline", "tts_ready"], default="all")
    args = parser.parse_args()

    rows = await run_bench(
        samples=SAMPLES,
        include_baseline=args.variant in {"all", "baseline"},
        include_tts_ready=args.variant in {"all", "tts_ready"},
    )
    write_outputs(rows, Path(args.output_dir))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

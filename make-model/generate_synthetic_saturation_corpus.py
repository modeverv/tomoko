#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from make_model.corpus import build_prefix_examples
from make_model.schema import CorpusUtterance, write_jsonl

SYNTHETIC_SOURCE = "synthetic-codex-saturation-v1"

TOPICS = [
    "今日の予定",
    "明日の予定",
    "このエラー",
    "このログ",
    "次の手順",
    "今の返事",
    "さっきの話",
    "買い物リスト",
    "会議の時間",
    "この設定",
    "評価結果",
    "推論結果",
    "会話サマリー",
    "調査タスク",
    "ライセンス",
    "ネットスーパーの注文",
    "万年筆のインク",
    "テレビ",
    "YouTube",
    "家庭教師の問題",
]

REQUEST_PATTERNS = [
    "{topic}を教えて",
    "{topic}を短く教えて",
    "{topic}を一言でまとめて",
    "{topic}を確認して",
    "{topic}を調べて",
    "{topic}を見て",
    "{topic}をお願い",
    "{topic}を直して",
    "{topic}を保存して",
    "{topic}を比較して",
    "{topic}はありますか",
    "{topic}ってどうですか",
    "{topic}で大丈夫ですか",
    "{topic}について話していい",
]

STATEMENT_PATTERNS = [
    "{topic}は大丈夫です",
    "{topic}は問題ないと思う",
    "{topic}は少し気になる",
    "{topic}は後で見ます",
    "{topic}は今確認中です",
    "{topic}はたぶん合っている",
    "{topic}はもう終わりました",
    "{topic}はまだ決めていません",
]

LOW_FRAGMENTS = [
    "えっと",
    "あの",
    "ただ",
    "でも",
    "いや",
    "というか",
    "なんかさ",
    "それで",
    "だから",
    "一個だけ",
    "ひとつだけ",
    "昨日の",
    "さっきの",
    "今の",
    "それは",
    "この前の",
    "ちょっと待って",
    "うーん",
    "まあ",
    "要するに",
]

LOW_SUFFIXES = [
    "",
    "、その",
    "、えーと",
    "、やっぱり",
    "、まだ",
    "、途中で",
    "、なんだけど",
    "、つまり",
    "、あれ",
    "、なんか",
]

CONTRASTIVE_BASES = [
    "それが良いと思う",
    "今日は進めたい",
    "この案でいい",
    "予定は合っている",
    "返事はこれでいい",
    "この設定で大丈夫",
    "いったん保存したい",
    "話は分かった",
    "この説明で足りる",
    "今の状態で十分",
]

CONTRASTIVE_ENDINGS = [
    "がしかし",
    "けど",
    "だけど",
    "ですが",
    "だが",
    "とはいえ",
    "ものの",
    "、でも",
    "、ただ",
    "、しかし",
]

REFERENTIAL_SUBJECTS = [
    "それ",
    "それが",
    "それは",
    "それで",
    "その方向",
    "その案",
    "その予定",
    "これ",
    "これが",
    "これは",
    "これで",
    "この案",
    "この方向",
    "今の判断",
    "さっきの話",
]

REFERENTIAL_PREDICATES = [
    "良いと思う",
    "いいと思う",
    "正しいと思う",
    "合っていると思う",
    "大丈夫だと思う",
    "問題ないと思う",
    "便利だと思う",
    "使えると思う",
    "良さそう",
    "大丈夫そう",
    "問題なさそう",
    "いい",
    "大丈夫",
    "問題ない",
    "違うと思う",
]

TOMOKO_LIFE_COMMANDS = [
    "トモコ、今忙しい",
    "トモコ、今何してる",
    "トモコ、ちょっと調査頼みたい",
    "トモコ、さっきの調査をまとめて",
    "トモコ、いつもの場所に置いておいて",
    "トモコ、テレビ付けて",
    "トモコ、YouTubeでおすすめのアニメ開いて",
    "トモコ、万年筆のインクを注文候補に入れて",
    "トモコ、ネットスーパーのラフを作って",
    "トモコ、今ちゃんと聞いてなかったでしょ",
    "トモコ、ちょっとちゃんと聞いて",
    "トモコ、今のは早とちりでしょ",
]


@dataclass(frozen=True, slots=True)
class SyntheticBuildSummary:
    utterance_count: int
    prefix_count: int
    source: str
    min_chars: int
    stride_chars: int
    max_prefixes_per_utterance: int | None
    seed: int


def build_synthetic_utterances(*, utterance_count: int, seed: int) -> list[CorpusUtterance]:
    if utterance_count <= 0:
        raise ValueError("utterance_count must be positive")

    texts: list[str] = []
    for topic in TOPICS:
        texts.extend(pattern.format(topic=topic) for pattern in REQUEST_PATTERNS)
        texts.extend(pattern.format(topic=topic) for pattern in STATEMENT_PATTERNS)
    for fragment in LOW_FRAGMENTS:
        texts.extend(f"{fragment}{suffix}" for suffix in LOW_SUFFIXES)
    for base in CONTRASTIVE_BASES:
        texts.append(base)
        texts.extend(f"{base}{ending}" for ending in CONTRASTIVE_ENDINGS)
    for subject in REFERENTIAL_SUBJECTS:
        texts.extend(f"{subject}{predicate}" for predicate in REFERENTIAL_PREDICATES)
    texts.extend(TOMOKO_LIFE_COMMANDS)

    expanded: list[str] = []
    polite_prefixes = ["", "ねえ、", "あのさ、", "ちょっと、"]
    polite_suffixes = ["", "ください", "お願い", "今で大丈夫"]
    for text in texts:
        expanded.append(text)
        if len(text) >= 5:
            for prefix in polite_prefixes:
                expanded.append(f"{prefix}{text}")
            for suffix in polite_suffixes:
                if suffix:
                    expanded.append(f"{text}、{suffix}")

    unique_texts = _unique_texts(expanded)
    rng = random.Random(seed)
    rng.shuffle(unique_texts)
    if len(unique_texts) < utterance_count:
        raise ValueError(f"only built {len(unique_texts)} unique synthetic utterances")
    selected = sorted(unique_texts[:utterance_count])
    return [
        CorpusUtterance(
            text=text,
            source=SYNTHETIC_SOURCE,
            utterance_id=f"synthetic-codex-{index:05d}",
        )
        for index, text in enumerate(selected, start=1)
    ]


def write_synthetic_dataset(
    *,
    out_dir: Path,
    utterance_count: int,
    seed: int,
    min_chars: int,
    stride_chars: int,
    max_prefixes_per_utterance: int | None,
) -> SyntheticBuildSummary:
    utterances = build_synthetic_utterances(utterance_count=utterance_count, seed=seed)
    prefixes = build_prefix_examples(
        utterances,
        min_chars=min_chars,
        stride_chars=stride_chars,
        include_final=True,
        max_prefixes_per_utterance=max_prefixes_per_utterance,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "corpus.jsonl", (utterance.to_json() for utterance in utterances))
    write_jsonl(out_dir / "prefixes.jsonl", (prefix.to_json() for prefix in prefixes))
    summary = SyntheticBuildSummary(
        utterance_count=len(utterances),
        prefix_count=len(prefixes),
        source=SYNTHETIC_SOURCE,
        min_chars=min_chars,
        stride_chars=stride_chars,
        max_prefixes_per_utterance=max_prefixes_per_utterance,
        seed=seed,
    )
    manifest = {
        **asdict(summary),
        "text_provenance": (
            "Synthetic Japanese utterance fragments generated by repository script from "
            "Codex/user-authored templates. No internet dialogue corpus is used."
        ),
        "intended_use": "Tomoko semantic saturation teacher input.",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _unique_texts(texts: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic-only semantic saturation corpus."
    )
    parser.add_argument(
        "--out-dir",
        default=Path("make-model/data/public-synthetic"),
        type=Path,
    )
    parser.add_argument("--utterance-count", default=2500, type=int)
    parser.add_argument("--seed", default=20260620, type=int)
    parser.add_argument("--min-chars", default=1, type=int)
    parser.add_argument("--stride-chars", default=1, type=int)
    parser.add_argument("--max-prefixes-per-utterance", default=80, type=int)
    args = parser.parse_args()

    summary = write_synthetic_dataset(
        out_dir=args.out_dir,
        utterance_count=args.utterance_count,
        seed=args.seed,
        min_chars=args.min_chars,
        stride_chars=args.stride_chars,
        max_prefixes_per_utterance=args.max_prefixes_per_utterance,
    )
    print(
        f"wrote {summary.utterance_count} utterances and {summary.prefix_count} prefixes "
        f"to {args.out_dir}"
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from make_model.append_dedupe import AppendDedupeExample
from make_model.schema import write_jsonl

APPEND_DEDUPE_SOURCE = "public_synthetic_append_dedupe_anchor"

DUPLICATE_PAIRS = (
    ("うんあんまりよくわかってない", "あんまりよくわかってない"),
    ("えっと今日は寒い", "今日は寒い"),
    ("あの今日の予定を教えて", "今日の予定を教えて"),
    ("まあ音量下げて", "音量下げて"),
    ("なんか設定ファイルの話", "設定ファイルの話"),
    ("テレビ付けて", "うんテレビ付けて"),
)
CONTINUATION_PAIRS = (
    ("あんまりよくわかってない", "もう少し具体的に言うと設定ファイルの話"),
    ("昨日の件なんだけど", "補足するとログの時刻の話"),
    ("ちょっと曖昧", "つまり runtime 側じゃなくて shadow 評価の話"),
    ("よくわからない", "というのはモデル artifact の作り方"),
    ("その説明だと微妙", "言い換えると否定ではなく追加条件"),
)
NEW_INTENT_PAIRS = (
    ("今日の予定を教えて", "ところで音量下げて"),
    ("設定ファイルの話をして", "テレビ付けて"),
    ("ログを見て", "別件で明日の予定を教えて"),
    ("今日寒いね", "タイマーを五分にして"),
    ("予定を教えて", "話変わるけどライト消して"),
)
CORRECTION_PAIRS = (
    ("今日は予定を教えて", "いや予定じゃなくて音量を下げて"),
    ("テレビ付けて", "違う、テレビじゃなくてライト"),
    ("設定ファイルを見て", "訂正、ログファイルのほう"),
    ("音量を上げて", "やっぱり下げて"),
)


def build_append_dedupe_labels(*, repeats: int = 16) -> list[AppendDedupeExample]:
    rows: list[AppendDedupeExample] = []
    groups = [
        ("duplicate", DUPLICATE_PAIRS),
        ("continuation", CONTINUATION_PAIRS),
        ("new_intent", NEW_INTENT_PAIRS),
        ("new_intent", CORRECTION_PAIRS),
    ]
    for repeat in range(repeats):
        time_delta_ms = 650 + (repeat % 6) * 450
        tomoko_speaking = repeat % 2 == 0
        speech_queue_active = repeat % 3 != 0
        for label, pairs in groups:
            for pair_index, (previous, current) in enumerate(pairs):
                rows.append(
                    AppendDedupeExample(
                        previous_user_text=previous,
                        current_user_text=current,
                        label=label,
                        time_delta_ms=time_delta_ms,
                        tomoko_speaking=tomoko_speaking,
                        speech_queue_active=speech_queue_active,
                        current_is_final=True,
                        source=APPEND_DEDUPE_SOURCE,
                        example_id=(
                            f"append-dedupe-{label}-{repeat:03d}-{pair_index:03d}"
                        ),
                    )
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate public synthetic append dedupe labels.",
    )
    parser.add_argument(
        "--out",
        default=Path("make-model/data/public-synthetic/append-dedupe-labels.jsonl"),
        type=Path,
    )
    parser.add_argument("--repeats", default=16, type=int)
    args = parser.parse_args()

    labels = build_append_dedupe_labels(repeats=args.repeats)
    write_jsonl(args.out, [label.to_json() for label in labels])
    print(f"wrote {len(labels)} append dedupe labels to {args.out}")


if __name__ == "__main__":
    main()

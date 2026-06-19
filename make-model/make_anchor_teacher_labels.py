#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from make_model.schema import TeacherLabel, write_jsonl

MANUAL_TEACHER_MODEL = "manual-anchor-v1"

HIGH_ANCHORS = [
    ("今日の予定を教えて", 0.95),
    ("聞こえますか", 0.90),
    ("今の返事ちゃんと聞こえてる", 0.85),
    ("いますか", 0.88),
    ("どうですか", 0.88),
    ("お願い", 0.86),
    ("短く答えて", 0.92),
    ("それで大丈夫ですか", 0.90),
    ("これでいいですか", 0.90),
    ("何時ですか", 0.94),
]

LOW_ANCHORS = [
    ("えっと", 0.10),
    ("あの", 0.10),
    ("ただ、やっぱり", 0.20),
    ("でも", 0.18),
    ("というか", 0.18),
    ("一個だけ", 0.25),
    ("ひとつだけ", 0.25),
    ("それで、その", 0.22),
    ("昨日の", 0.25),
    ("なんかさ", 0.20),
]

MID_ANCHORS = [
    ("今日は少し眠いです", 0.55),
    ("今は大丈夫です", 0.62),
    ("それは少し違うと思います", 0.62),
    ("たぶん明日になります", 0.58),
    ("もう少し考えています", 0.48),
]

TOPICS = [
    "今日の予定",
    "明日の予定",
    "次の予定",
    "今の状態",
    "この意味",
    "この文章",
    "今の返事",
    "さっきの話",
    "昨日の続き",
    "会議の時間",
    "天気",
    "ニュース",
    "やること",
    "買い物リスト",
    "リマインダー",
    "タイマー",
    "アラーム",
    "予定の変更",
    "今何時か",
    "この設定",
    "このエラー",
    "使い方",
    "次の手順",
    "結論",
    "理由",
    "違い",
    "要点",
    "おすすめ",
    "確認事項",
    "返事",
    "今日のタスク",
    "明日の準備",
    "次の会議",
    "昼の予定",
    "夜の予定",
    "今の画面",
    "このコード",
    "このログ",
    "この予定",
    "このメモ",
    "話の続き",
    "今の質問",
    "次にやること",
    "今日の優先順位",
    "作業内容",
    "変更点",
    "評価結果",
    "学習結果",
    "推論結果",
    "確認結果",
]

HIGH_PATTERNS = [
    "{topic}を教えて",
    "{topic}について教えて",
    "{topic}を一言で教えて",
    "{topic}を短く教えて",
    "{topic}を確認して",
    "{topic}を見て",
    "{topic}をお願い",
    "{topic}はありますか",
    "{topic}ってどうですか",
    "{topic}で大丈夫ですか",
    "{topic}をまとめて",
    "{topic}を説明して",
    "{topic}を直して",
    "{topic}を考えて",
    "{topic}を覚えておいて",
    "{topic}をチェックして",
    "{topic}を調べて",
    "{topic}を見直して",
    "{topic}を比較して",
    "{topic}を評価して",
    "{topic}を保存して",
    "{topic}を読んで",
    "{topic}をもう一度教えて",
    "{topic}を今教えて",
    "{topic}を簡単に教えて",
]

LOW_PREFIXES = [
    "えっと",
    "あの",
    "ただ",
    "でも",
    "いや",
    "というか",
    "一個だけ",
    "ひとつだけ",
    "それで",
    "なんか",
    "だから",
    "昨日の",
    "さっきの",
    "今の",
    "それは",
]

LOW_SUFFIXES = [
    "",
    "、その",
    "、やっぱり",
    "、えーと",
    "、まだ",
    "、途中で",
    "、ちょっと",
    "、つまり",
    "、なんだけど",
    "、あれ",
]

MID_PATTERNS = [
    "{topic}はたぶん大丈夫です",
    "{topic}は少し気になります",
    "{topic}は後で見ます",
    "{topic}はまだ決めていません",
    "{topic}は一応あります",
    "{topic}は今確認中です",
    "{topic}は少し待っています",
    "{topic}はだいたい分かりました",
]


def build_anchor_labels(*, count: int = 1000) -> list[TeacherLabel]:
    if count <= 0:
        raise ValueError("count must be positive")

    rows: list[tuple[str, float, bool]] = []
    rows.extend((text, saturation, True) for text, saturation in HIGH_ANCHORS)
    rows.extend((text, saturation, False) for text, saturation in LOW_ANCHORS)
    rows.extend((text, saturation, True) for text, saturation in MID_ANCHORS)

    for topic in TOPICS:
        for pattern in HIGH_PATTERNS:
            rows.append((pattern.format(topic=topic), 0.92, True))
    for prefix in LOW_PREFIXES:
        for suffix in LOW_SUFFIXES:
            rows.append((f"{prefix}{suffix}", 0.18 if suffix else 0.12, False))
    for topic in TOPICS:
        for pattern in MID_PATTERNS:
            rows.append((pattern.format(topic=topic), 0.58, True))

    unique_rows: list[tuple[str, float, bool]] = []
    seen: set[str] = set()
    for text, saturation, is_final in rows:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_rows.append((normalized, saturation, is_final))
        if len(unique_rows) >= count:
            break
    if len(unique_rows) < count:
        raise ValueError(f"only built {len(unique_rows)} unique anchors")

    return [
        TeacherLabel(
            utterance_id=f"manual-anchor-{index:04d}",
            prefix_index=0,
            prefix_text=text,
            full_text=text,
            saturation=saturation,
            teacher_model=MANUAL_TEACHER_MODEL,
            source="manual-anchor:v1",
            is_final=is_final,
            label_source="manual_anchor",
            raw_output=f"SATURATION={saturation:.2f}",
        )
        for index, (text, saturation, is_final) in enumerate(unique_rows, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Write handcrafted saturation anchor labels.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", default=1000, type=int)
    args = parser.parse_args()

    anchors = build_anchor_labels(count=args.count)
    write_jsonl(args.out, (anchor.to_json() for anchor in anchors))
    print(f"wrote {len(anchors)} manual anchor labels to {args.out}")


if __name__ == "__main__":
    main()

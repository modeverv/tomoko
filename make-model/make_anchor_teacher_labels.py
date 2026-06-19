#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from make_model.schema import TeacherLabel, write_jsonl

MANUAL_TEACHER_MODEL = "manual-anchor-v1"
CONTRASTIVE_TEACHER_MODEL = "manual-contrastive-anchor-v1"
REFERENTIAL_TEACHER_MODEL = "manual-referential-anchor-v1"

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

CONTRASTIVE_BASES = [
    "それが良いと思う",
    "今日は進めたい",
    "この案でいい",
    "予定は合っている",
    "返事はこれでいい",
    "明日は空いている",
    "この設定で大丈夫",
    "いったん保存したい",
    "このまま進めたい",
    "それは正しい",
    "結論は出ている",
    "準備はできている",
    "話は分かった",
    "内容は理解した",
    "予定は決まった",
    "今日は行ける",
    "今ならできる",
    "それで問題ない",
    "この説明で足りる",
    "その方向でよい",
    "タスクは終わった",
    "確認はできた",
    "返答は短くていい",
    "この手順で進む",
    "だいたい合っている",
    "今の判断でいい",
    "その予定でいい",
    "一旦これで進める",
    "今日は休める",
    "もう大丈夫",
    "このコードで動く",
    "ログは問題ない",
    "評価は悪くない",
    "学習は進んでいる",
    "精度は上がっている",
    "速度は十分速い",
    "この返事でいい",
    "今の説明でいい",
    "予定を見れば分かる",
    "今日はその方がいい",
    "短く答えればいい",
    "それは便利だ",
    "結果は出ている",
    "データは足りている",
    "ファイルは揃っている",
    "推論は成功している",
    "評価は通っている",
    "テストは通っている",
    "このモデルで使える",
    "今の状態で十分",
    "方向性は良い",
    "話としては自然",
    "返し始めてもよい",
    "意味は取れている",
    "質問として成立している",
    "依頼として分かる",
    "確認として分かる",
    "答えはありそう",
    "予定は知りたい",
    "説明はできる",
    "今日は大丈夫",
    "それはそう",
    "今なら分かる",
    "もう聞こえている",
    "ちゃんと届いている",
    "このままでも良い",
    "追加しなくていい",
    "変更しなくていい",
    "覚えておけばいい",
    "後で見ればいい",
    "返してもよさそう",
    "話してもよさそう",
    "始めてもよさそう",
    "確認してもよさそう",
    "教えてもらえそう",
    "お願いできそう",
]

CONTRASTIVE_ENDINGS = [
    "がしかし",
    "が",
    "けど",
    "だけど",
    "ですが",
    "だが",
    "とはいえ",
    "ものの",
    "、でも",
    "、ただ",
    "、しかし",
    "。でも",
    "。ただ",
    "。しかし",
    "。だけど",
]

CONTRASTIVE_SATURATION = {
    "がしかし": 0.22,
    "が": 0.26,
    "けど": 0.24,
    "だけど": 0.24,
    "ですが": 0.24,
    "だが": 0.24,
    "とはいえ": 0.28,
    "ものの": 0.28,
    "、でも": 0.20,
    "、ただ": 0.20,
    "、しかし": 0.20,
    "。でも": 0.18,
    "。ただ": 0.18,
    "。しかし": 0.18,
    "。だけど": 0.18,
}

REFERENTIAL_ANCHORS = [
    ("それが良いと思う", 0.82),
    ("それで問題ない", 0.85),
    ("その方向でいい", 0.84),
    ("これは違うと思う", 0.78),
    ("それでいいと思う", 0.84),
    ("それは正しいと思う", 0.82),
    ("その案でいいと思う", 0.84),
    ("このままで大丈夫", 0.82),
    ("これで大丈夫", 0.84),
    ("それで合っている", 0.83),
]

REFERENTIAL_SUBJECTS = [
    "それ",
    "それが",
    "それは",
    "それで",
    "その方向",
    "その案",
    "その予定",
    "その判断",
    "その説明",
    "その返事",
    "これ",
    "これが",
    "これは",
    "これで",
    "この案",
    "この方向",
    "この予定",
    "この判断",
    "この説明",
    "この返事",
    "あれ",
    "あれは",
    "あの案",
    "あの方向",
    "今の案",
    "今の判断",
    "今の説明",
    "さっきの案",
    "さっきの話",
    "話の流れ",
    "今の流れ",
    "その流れ",
    "この流れ",
    "その内容",
    "この内容",
    "その方法",
    "この方法",
    "そのやり方",
    "このやり方",
    "今のやり方",
]

REFERENTIAL_PREDICATES = [
    ("良いと思う", 0.82),
    ("いいと思う", 0.82),
    ("正しいと思う", 0.82),
    ("合っていると思う", 0.82),
    ("大丈夫だと思う", 0.80),
    ("問題ないと思う", 0.82),
    ("自然だと思う", 0.78),
    ("便利だと思う", 0.78),
    ("使えると思う", 0.80),
    ("良さそう", 0.78),
    ("よさそう", 0.78),
    ("大丈夫そう", 0.78),
    ("問題なさそう", 0.80),
    ("合っていそう", 0.78),
    ("正しそう", 0.78),
    ("いい", 0.82),
    ("良い", 0.82),
    ("大丈夫", 0.82),
    ("問題ない", 0.85),
    ("合っている", 0.83),
    ("正しい", 0.82),
    ("違うと思う", 0.78),
    ("少し違うと思う", 0.74),
    ("それでいい", 0.84),
    ("このままでいい", 0.82),
    ("そのままでいい", 0.82),
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


def build_contrastive_anchor_labels(*, count: int = 1000) -> list[TeacherLabel]:
    if count <= 0:
        raise ValueError("count must be positive")

    rows: list[tuple[str, float]] = [("それが良いと思うがしかし", 0.22)]
    for base in CONTRASTIVE_BASES:
        for ending in CONTRASTIVE_ENDINGS:
            rows.append((f"{base}{ending}", CONTRASTIVE_SATURATION[ending]))

    unique_rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for text, saturation in rows:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_rows.append((normalized, saturation))
        if len(unique_rows) >= count:
            break
    if len(unique_rows) < count:
        raise ValueError(f"only built {len(unique_rows)} unique contrastive anchors")

    return [
        TeacherLabel(
            utterance_id=f"manual-contrastive-anchor-{index:04d}",
            prefix_index=0,
            prefix_text=text,
            full_text=text,
            saturation=saturation,
            teacher_model=CONTRASTIVE_TEACHER_MODEL,
            source="manual-contrastive-anchor:v1",
            is_final=True,
            label_source="manual_contrastive_anchor",
            raw_output=f"SATURATION={saturation:.2f}",
        )
        for index, (text, saturation) in enumerate(unique_rows, start=1)
    ]


def build_referential_anchor_labels(*, count: int = 1000) -> list[TeacherLabel]:
    if count <= 0:
        raise ValueError("count must be positive")

    rows: list[tuple[str, float]] = list(REFERENTIAL_ANCHORS)
    for subject in REFERENTIAL_SUBJECTS:
        for predicate, saturation in REFERENTIAL_PREDICATES:
            rows.append((f"{subject}{predicate}", saturation))

    unique_rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for text, saturation in rows:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_rows.append((normalized, saturation))
        if len(unique_rows) >= count:
            break
    if len(unique_rows) < count:
        raise ValueError(f"only built {len(unique_rows)} unique referential anchors")

    return [
        TeacherLabel(
            utterance_id=f"manual-referential-anchor-{index:04d}",
            prefix_index=0,
            prefix_text=text,
            full_text=text,
            saturation=saturation,
            teacher_model=REFERENTIAL_TEACHER_MODEL,
            source="manual-referential-anchor:v1",
            is_final=True,
            label_source="manual_referential_anchor",
            raw_output=f"SATURATION={saturation:.2f}",
        )
        for index, (text, saturation) in enumerate(unique_rows, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Write handcrafted saturation anchor labels.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", default=1000, type=int)
    parser.add_argument(
        "--kind",
        default="general",
        choices=("general", "contrastive", "referential"),
    )
    args = parser.parse_args()

    if args.kind == "contrastive":
        anchors = build_contrastive_anchor_labels(count=args.count)
    elif args.kind == "referential":
        anchors = build_referential_anchor_labels(count=args.count)
    else:
        anchors = build_anchor_labels(count=args.count)
    write_jsonl(args.out, (anchor.to_json() for anchor in anchors))
    print(f"wrote {len(anchors)} manual anchor labels to {args.out}")


if __name__ == "__main__":
    main()

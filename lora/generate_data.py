#!/usr/bin/env python3

import argparse
import json
import os
import random
import re
from typing import Any

import httpx
from tqdm import tqdm

# 有効な emotion と禁止 emotion のリマップ
VALID_EMOTIONS = {"neutral", "happy", "surprised", "sad", "thinking", "gentle", "excited"}
EMOTION_REMAP = {
    "playful": "happy",
    "angry": "neutral",
    "embarrassed": "gentle",
    "confused": "thinking",
    "curious": "thinking",
    "anxious": "sad",
    "bored": "neutral",
    "tired": "gentle",
}


def strip_thinking_block(text: str) -> str:
    """<|channel|>thought ... ブロックを除去し、実際の応答だけを返す。"""
    # <|channel|>thought で始まるブロックを削除
    # パターン: <|channel|>thought\n...\n<|channel|> or end
    text = re.sub(r'<\|channel\|>thought.*?(?=<\|channel\|>(?!thought)|\Z)', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|channel\|>', '', text)
    return text.strip()


def extract_final_response(text: str) -> str:
    """thinking ブロック混じりのテキストから最終的な EMOTION:xxx \n 本文 を抽出する。"""
    cleaned = strip_thinking_block(text)
    if cleaned and cleaned.startswith("EMOTION:"):
        return cleaned

    # thinking ブロックが strip できなかった場合: EMOTION: から始まる最後のブロックを探す
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip().lstrip("* \t")
        if stripped.startswith("EMOTION:"):
            # ここから後続の本文行を集める
            body_lines = [stripped]
            for j in range(i + 1, min(i + 5, len(lines))):
                next_line = lines[j].strip().lstrip("* \t")
                if next_line and not next_line.startswith("EMOTION:") and not next_line.startswith("*"):
                    body_lines.append(next_line)
                else:
                    break
            candidate = "\n".join(body_lines)
            if candidate.split("\n")[0].replace("EMOTION:", "").strip() in VALID_EMOTIONS | set(EMOTION_REMAP):
                return candidate
    return ""


def sanitize_emotion(response: str) -> str:
    """EMOTION: 行の emotion を有効な値に修正する。無効なら None を返す。"""
    lines = response.strip().split("\n")
    if not lines:
        return ""
    first = lines[0].strip()
    if not first.startswith("EMOTION:"):
        return ""  # EMOTION 行なし → 無効サンプル
    emotion = first.replace("EMOTION:", "").strip()
    if emotion in VALID_EMOTIONS:
        return response  # そのまま OK
    if emotion in EMOTION_REMAP:
        lines[0] = f"EMOTION:{EMOTION_REMAP[emotion]}"
        return "\n".join(lines)
    return ""  # リマップ不可の emotion → 破棄


# シードとなるユーザー発話リスト
SEED_PROMPTS = [
    "こんにちは！",
    "自己紹介をしてくれる？",
    "あなたについて教えて。",
    "今日の予定を教えて。",
    "疲れたなー、癒やして",
    "タスクを一つ追加しておいて。ブログ記事の執筆",
    "今何時？",
    "何が得意なの？",
    "これからよろしくね",
    "ちょっと聞いてよ、今日いいことがあったんだ",
    "将来の夢ってある？",
    "勉強のやる気が出ないんだけど、どうすればいい？",
    "おすすめの本を教えて",
    "今日のご飯、何がいいと思う？",
    "最近おもしろいニュースあった？",
    "気分が落ち込んでいるんだ",
    "今度の土曜日空いてる？",
    "プログラミングのコツを教えて",
    "ちょっと雑談しよう",
    "お腹が空いたなー",
    "最近眠れないんだよね",
    "何か面白い話をして",
    "私の名前を覚えてる？",
    "どんな音楽が好き？",
]

def load_system_prompt(path: str, overlay_path: str | None = None) -> str:
    """システムプロンプトファイルと、オプションで人格オーバーレイファイルを読み込んで結合する。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"System prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        system_prompt = f.read().strip()

    # overlay_path が明示されていない場合は、path の sibling である persona_overlay.md を自動探索
    if overlay_path is None:
        sibling_overlay = os.path.join(os.path.dirname(path), "persona_overlay.md")
        if os.path.exists(sibling_overlay):
            overlay_path = sibling_overlay

    if overlay_path and overlay_path.lower() != "none" and os.path.exists(overlay_path):
        print(f"Loading persona overlay: {overlay_path}")
        with open(overlay_path, encoding="utf-8") as f:
            overlay_content = f.read().strip()
            if overlay_content:
                system_prompt = f"{system_prompt}\n\n{overlay_content}"

    return system_prompt

def generate_user_prompts_ollama(
    base_url: str, model: str, count: int, seed_prompts: list[str]
) -> list[str]:
    """Ollamaを使用して多様なユーザー発話を拡張生成する。"""
    print(f"Generating {count} user prompts using Ollama ({model})...")
    
    prompt = (
        "あなたはAI対話システムのユーザーシミュレータです。以下のシード発話を参考に、"
        "ユーザーが対話システム（キャラクターやアシスタント）に対して日常的に投げかける可能性のある、"
        "自然で多様な日本語の質問や話しかけ、指示などを生成してください。\n\n"
        "シード発話の例:\n" + "\n".join(f"- {p}" for p in seed_prompts[:10]) + "\n\n"
        f"要件:\n"
        f"1. 重複がなく、口調やジャンル（雑談、質問、相談、タスク依頼など）が多様であること。\n"
        f"2. {count}個のユニークな発話を生成してください。\n"
        f"3. 出力は、1行に1つの発話のみとし、番号や箇条書きの記号（1., -, * など）は"
        f"一切含めないでください。余計な説明文も不要です。"
    )
    
    try:
        response = httpx.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.8,
                }
            },
            timeout=60.0
        )
        response.raise_for_status()
        text = response.json().get("response", "")
        
        # 行ごとに分割してクリーンアップ
        generated = []
        for line in text.split("\n"):
            line = line.strip()
            # 番号や箇条書き記号の除去（簡単な正規化）
            if not line:
                continue
            # 先頭の箇条書きマークや数字を消す
            for prefix in ["-", "*", "•"]:
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
            # 数字のピリオドや括弧付き数字を消す
            import re
            line = re.sub(r'^\d+[\.\)\]\-、\s]+', '', line).strip()
            
            if line and len(line) > 2:
                generated.append(line)
                
        # 重複排除
        generated = list(set(generated))
        print(f"Successfully generated {len(generated)} custom user prompts.")
        return generated
    except Exception as e:
        print(f"Warning: Failed to generate prompts via Ollama ({e}). Using seed prompts.")
        return []

def generate_user_prompts_mlx(
    model_path: str, count: int, seed_prompts: list[str]
) -> list[str]:
    """MLX-LMを使用して多様なユーザー発話を拡張生成する。"""
    try:
        from mlx_lm import generate, load
    except ImportError:
        print("mlx-lm package is not installed. Skipping MLX user prompt generation.")
        return []
        
    print(f"Loading MLX model {model_path} for prompt generation...")
    model, tokenizer = load(model_path)
    
    prompt = (
        "以下のシード発話を参考に、ユーザーが対話システムに対して投げかける可能性のある、"
        "自然で多様な日本語の発話（雑談、質問、挨拶など）を生成してください。\n\n"
        "シード発話:\n" + "\n".join(f"- {p}" for p in seed_prompts[:10]) + "\n\n"
        f"必ず{count}個生成し、余計な説明は省き、1行に1発話ずつ出力してください。番号などは不要です。"
    )
    
    try:
        from mlx_lm.sample_utils import make_sampler
        sampler = make_sampler(temp=0.8)
        gen_kwargs = {"sampler": sampler}
    except ImportError:
        gen_kwargs = {"temperature": 0.8}

    response = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=2048,
        **gen_kwargs
    )
    
    generated = []
    for line in response.split("\n"):
        line = line.strip()
        if not line:
            continue
        import re
        line = re.sub(r'^\d+[\.\)\]\-、\s]+', '', line).strip()
        for prefix in ["-", "*", "•"]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        if line and len(line) > 2:
            generated.append(line)
            
    return list(set(generated))

def get_assistant_response_ollama(
    base_url: str, model: str, system_prompt: str, user_prompt: str
) -> str:
    """Ollamaを使用して、システムプロンプトに従ったアシスタントの応答を生成する。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                }
            },
            timeout=30.0
        )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"Error calling Ollama chat for user prompt '{user_prompt}': {e}")
        return ""

def get_assistant_response_openai_compat(
    base_url: str, model: str, system_prompt: str, user_prompt: str
) -> str:
    """OpenAI 互換エンドポイント（LM Studio / dflash 等）を使って応答を生成する。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.7,
        "max_tokens": 256,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        url = base_url.rstrip("/") + "/v1/chat/completions"
        resp = httpx.post(url, json=payload, timeout=60.0)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"Error calling openai_compat for '{user_prompt}': {e}")
        return ""


def get_assistant_response_mlx(
    model: Any, tokenizer: Any, system_prompt: str, user_prompt: str
) -> str:
    """MLX-LMを使用して、システムプロンプトに従ったアシスタントの応答を生成する。"""
    try:
        from mlx_lm import generate
    except ImportError:
        return ""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # チャットテンプレートの適用（enable_thinking=False で thinking ブロックを無効化）
    if hasattr(tokenizer, "apply_chat_template"):
        try:
            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
                chat_template_kwargs={"enable_thinking": False},
            )
        except TypeError:
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
    else:
        prompt = (
            f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
            f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    try:
        from mlx_lm.sample_utils import make_sampler
        sampler = make_sampler(temp=0.7)
        gen_kwargs = {"sampler": sampler}
    except ImportError:
        gen_kwargs = {"temperature": 0.7}

    raw = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=512,
        **gen_kwargs,
    )
    # thinking ブロックを除去して最終応答だけを返す
    return extract_final_response(raw)


def main():
    parser = argparse.ArgumentParser(
        description="Generate dataset for LLM system prompt baking LoRA."
    )
    parser.add_argument("--system-prompt-path", type=str, default="prompts/base_persona.md",
                        help="Path to the system prompt file.")
    parser.add_argument("--output-dir", type=str, default="lora/data",
                        help="Directory to save train.jsonl and valid.jsonl.")
    parser.add_argument("--num-samples", type=int, default=100,
                        help="Target number of dialogue samples.")
    parser.add_argument("--backend", type=str, choices=["ollama", "mlx", "openai_compat"], default="ollama",
                        help="LLM backend (ollama / mlx / openai_compat).")
    parser.add_argument("--model", type=str, default="qwen2.5:7b",
                        help="Model name or path. For openai_compat, the model id on the server.")
    parser.add_argument("--ollama-url", type=str, default="http://localhost:11434",
                        help="Ollama base URL.")
    parser.add_argument("--openai-url", type=str, default="http://localhost:8082",
                        help="Base URL for openai_compat backend (e.g. dflash / LM Studio).")
    parser.add_argument(
        "--overlay-path", type=str, default=None,
        help=(
            "Path to the persona overlay file. "
            "Defaults to sibling persona_overlay.md if exists. "
            "Set to 'none' to disable."
        )
    )
    parser.add_argument("--split-ratio", type=float, default=0.9,
                        help="Ratio of train split (default: 0.9).")
    
    args = parser.parse_args()
    
    # 出力先ディレクトリの作成
    os.makedirs(args.output_dir, exist_ok=True)
    
    # システムプロンプトの読み込み
    try:
        system_prompt = load_system_prompt(args.system_prompt_path, args.overlay_path)
    except Exception as e:
        print(f"Error loading system prompt: {e}")
        return

    print("Loaded System Prompt:")
    print("-" * 40)
    print(system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)
    print("-" * 40)
    
    # ユーザー発話リストの収集と拡張
    user_prompts = list(SEED_PROMPTS)
    needed_extra = args.num_samples - len(user_prompts)
    
    if needed_extra > 0:
        if args.backend == "ollama":
            extra_prompts = generate_user_prompts_ollama(
                args.ollama_url,
                args.model,
                needed_extra * 2,
                SEED_PROMPTS
            )
            user_prompts.extend(extra_prompts)
        elif args.backend == "mlx":
            extra_prompts = generate_user_prompts_mlx(
                args.model,
                needed_extra * 2,
                SEED_PROMPTS
            )
            user_prompts.extend(extra_prompts)
        elif args.backend == "openai_compat":
            # openai_compat は seed プロンプトをそのまま使用（ユーザー発話生成は Ollama と同じロジックを拡張予定）
            pass
            
    # 重複排除と目標数へのクリップ
    user_prompts = list(set(user_prompts))
    random.shuffle(user_prompts)
    user_prompts = user_prompts[:args.num_samples]
    
    print(f"Total user prompts to process: {len(user_prompts)}")
    
    # 対話データの生成
    dataset: list[dict[str, Any]] = []
    
    # MLXモデルのロード（MLXバックエンドの場合のみ事前に1回ロード）
    mlx_model = None
    mlx_tokenizer = None
    if args.backend == "mlx":
        try:
            from mlx_lm import load
            print(f"Loading MLX model {args.model} for response generation...")
            mlx_model, mlx_tokenizer = load(args.model)
        except ImportError:
            print("Error: mlx-lm is not installed. Cannot use mlx backend.")
            return
            
    # 応答生成ループ
    skipped = 0
    for u_prompt in tqdm(user_prompts, desc="Generating responses"):
        if args.backend == "ollama":
            response = get_assistant_response_ollama(
                args.ollama_url,
                args.model,
                system_prompt,
                u_prompt,
            )
        elif args.backend == "openai_compat":
            response = get_assistant_response_openai_compat(
                args.openai_url,
                args.model,
                system_prompt,
                u_prompt,
            )
        else:  # mlx
            response = get_assistant_response_mlx(
                mlx_model,
                mlx_tokenizer,
                system_prompt,
                u_prompt,
            )

        if not response:
            skipped += 1
            continue

        # thinking ブロックが混入していたら抗出す
        response = extract_final_response(response) or response
        # emotion を有効値にサニタイズ（無効ならスキップ）
        response = sanitize_emotion(response)
        if not response:
            skipped += 1
            continue

        if response:
            sample = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": u_prompt},
                    {"role": "assistant", "content": response}
                ]
            }
            dataset.append(sample)
            
    print(f"Generated {len(dataset)} valid dialogue samples. (skipped {skipped} invalid)")
    
    if not dataset:
        print("Error: No data was generated. Dataset is empty.")
        return
        
    # データのシャッフルと分割
    random.shuffle(dataset)
    split_idx = int(len(dataset) * args.split_ratio)
    train_data = dataset[:split_idx]
    val_data = dataset[split_idx:]
    
    # 保存
    train_path = os.path.join(args.output_dir, "train.jsonl")
    val_path = os.path.join(args.output_dir, "valid.jsonl")
    
    with open(train_path, "w", encoding="utf-8") as f:
        for item in train_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    with open(val_path, "w", encoding="utf-8") as f:
        for item in val_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    print("Dataset generated successfully!")
    print(f"Train split saved to: {train_path} ({len(train_data)} samples)")
    print(f"Validation split saved to: {val_path} ({len(val_data)} samples)")

if __name__ == "__main__":
    main()

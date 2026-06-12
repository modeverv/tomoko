#!/usr/bin/env python3

import argparse
import os
import sys

# デフォルトのテスト発話
DEFAULT_TEST_PROMPTS = [
    "こんにちは、あなたについて教えてください。",
    "今日はずっと寝ていたい気分なんだよね。",
    "自己紹介をお願いします。",
    "プログラミングの勉強をしているんだけど、難しくて挫けそう。",
    "お腹が空いた。何か美味しいもののアイデアはある？",
    "タスクに「本を読む」を追加して。",
]

def run_interactive(model, tokenizer):
    """ターミナルでインタラクティブな対話を行う。"""
    print("\n=== Interactive Chat Mode (type 'exit' or 'quit' to end) ===")
    print("Type your message and press Enter.\n")
    
    # 簡易履歴管理（システムプロンプトを焼き込んでいるため、本来は会話ログがなくても
    # システムプロンプトの指示に従った回答になるはず）
    chat_history = []
    
    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                break
                
            chat_history.append({"role": "user", "content": user_input})
            
            # MLX-LM の generate 呼び出し
            from mlx_lm import generate
            
            # チャットテンプレートの適用
            if hasattr(tokenizer, "apply_chat_template"):
                prompt = tokenizer.apply_chat_template(
                    chat_history, tokenize=False, add_generation_prompt=True
                )
            else:
                prompt = f"<|im_start|>user\n{user_input}<|im_end|>\n<|im_start|>assistant\n"
                
            print("AI: ", end="", flush=True)
            
            # ストリーミングのような動作を再現（簡略化のため通常生成）
            try:
                from mlx_lm.sample_utils import make_sampler
                sampler = make_sampler(temp=0.7)
                gen_kwargs = {"sampler": sampler}
            except ImportError:
                gen_kwargs = {"temperature": 0.7}

            response = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=512,
                **gen_kwargs
            )
            print(response)
            
            chat_history.append({"role": "assistant", "content": response.strip()})
            
        except KeyboardInterrupt:
            print("\nExiting chat mode...")
            break
        except Exception as e:
            print(f"\nError: {e}")
            break

def run_batch(model, tokenizer, test_prompts):
    """テスト発話リストに対して一括で応答を生成する。"""
    print("\n=== Batch Evaluation Mode ===")
    from mlx_lm import generate
    
    for i, prompt_text in enumerate(test_prompts, 1):
        print(f"\n[{i}/{len(test_prompts)}] User: {prompt_text}")
        
        messages = [{"role": "user", "content": prompt_text}]
        if hasattr(tokenizer, "apply_chat_template"):
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            prompt = f"<|im_start|>user\n{prompt_text}<|im_end|>\n<|im_start|>assistant\n"
            
        try:
            try:
                from mlx_lm.sample_utils import make_sampler
                sampler = make_sampler(temp=0.7)
                gen_kwargs = {"sampler": sampler}
            except ImportError:
                gen_kwargs = {"temperature": 0.7}

            response = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=512,
                **gen_kwargs
            )
            print(f"AI: {response.strip()}")
        except Exception as e:
            print(f"AI Error: {e}")
        print("-" * 50)

def main():
    parser = argparse.ArgumentParser(description="Evaluate fine-tuned LoRA model.")
    parser.add_argument("--model", type=str, default="mlx-community/Qwen2.5-7B-Instruct-4bit",
                        help="Base MLX model folder path or Hugging Face repo ID.")
    parser.add_argument("--adapter", type=str, default="loras/lora/adapters",
                        help="Path to the saved LoRA adapter directory.")
    parser.add_argument("--interactive", action="store_true",
                        help="Run in interactive chat mode.")
    parser.add_argument("--prompts", type=str, default=None,
                        help="Comma-separated list of custom test prompts for batch evaluation.")
    
    args = parser.parse_args()
    
    # mlx-lm がインストールされているか確認
    try:
        from mlx_lm import load
    except ImportError:
        print("Error: mlx-lm is not installed. Please run: pip install mlx-lm")
        sys.exit(1)
        
    print(f"Loading base model: {args.model}")
    if os.path.exists(args.adapter):
        print(f"Applying LoRA adapter from: {args.adapter}")
        try:
            model, tokenizer = load(args.model, adapter_path=args.adapter)
        except Exception as e:
            print(f"Error loading model with adapter: {e}")
            print("Loading base model without adapter as fallback...")
            model, tokenizer = load(args.model)
    else:
        print(f"Warning: Adapter path '{args.adapter}' not found. Loading base model only.")
        model, tokenizer = load(args.model)
        
    # テスト発話の構築
    if args.prompts:
        test_prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    else:
        test_prompts = DEFAULT_TEST_PROMPTS
        
    if args.interactive:
        run_interactive(model, tokenizer)
    else:
        run_batch(model, tokenizer, test_prompts)

if __name__ == "__main__":
    main()

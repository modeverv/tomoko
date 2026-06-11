from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

# lora ディレクトリをインポート対象に加える
sys.path.append(os.path.join(os.path.dirname(__file__), "../../lora"))

from generate_data import load_system_prompt, SEED_PROMPTS
import evaluate


@pytest.mark.unit
class TestLoraPrograms(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_prompt_path = "tests/unit/temp_test_prompt.md"
        with open(self.temp_prompt_path, "w", encoding="utf-8") as f:
            f.write("This is a test system prompt for LoRA baking.")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_prompt_path):
            os.remove(self.temp_prompt_path)

    def test_load_system_prompt_success(self) -> None:
        """システムプロンプトファイルが正しくロードできること"""
        prompt = load_system_prompt(self.temp_prompt_path)
        self.assertEqual(prompt, "This is a test system prompt for LoRA baking.")

    def test_load_system_prompt_not_found(self) -> None:
        """存在しないファイルパスの場合は FileNotFoundError を投げること"""
        with self.assertRaises(FileNotFoundError):
            load_system_prompt("non_existent_file.md")

    def test_load_system_prompt_with_custom_overlay(self) -> None:
        """明示的に overlay_path が指定された場合に、システムプロンプトとオーバーレイが結合されること"""
        custom_overlay_path = "tests/unit/temp_custom_overlay.md"
        try:
            with open(custom_overlay_path, "w", encoding="utf-8") as f:
                f.write("This is a custom overlay.")
            
            prompt = load_system_prompt(self.temp_prompt_path, custom_overlay_path)
            expected = "This is a test system prompt for LoRA baking.\n\nThis is a custom overlay."
            self.assertEqual(prompt, expected)
        finally:
            if os.path.exists(custom_overlay_path):
                os.remove(custom_overlay_path)

    def test_load_system_prompt_with_sibling_overlay(self) -> None:
        """overlay_pathがNoneの場合、隣にある persona_overlay.md が自動でロードされて結合されること"""
        sibling_overlay_path = "tests/unit/persona_overlay.md"
        try:
            with open(sibling_overlay_path, "w", encoding="utf-8") as f:
                f.write("This is a sibling overlay.")
            
            prompt = load_system_prompt(self.temp_prompt_path)
            expected = "This is a test system prompt for LoRA baking.\n\nThis is a sibling overlay."
            self.assertEqual(prompt, expected)
        finally:
            if os.path.exists(sibling_overlay_path):
                os.remove(sibling_overlay_path)

    def test_load_system_prompt_disable_overlay(self) -> None:
        """overlay_pathが'none'の場合、隣にある persona_overlay.md が自動でロードされず結合されないこと"""
        sibling_overlay_path = "tests/unit/persona_overlay.md"
        try:
            with open(sibling_overlay_path, "w", encoding="utf-8") as f:
                f.write("This is a sibling overlay.")
            
            prompt = load_system_prompt(self.temp_prompt_path, "none")
            expected = "This is a test system prompt for LoRA baking."
            self.assertEqual(prompt, expected)
        finally:
            if os.path.exists(sibling_overlay_path):
                os.remove(sibling_overlay_path)

    def test_seed_prompts_not_empty(self) -> None:
        """シード発話リストが空でないこと"""
        self.assertTrue(len(SEED_PROMPTS) > 0)
        for p in SEED_PROMPTS:
            self.assertIsInstance(p, str)

    @patch("httpx.post")
    def test_generate_user_prompts_ollama_fallback_on_error(self, mock_post: MagicMock) -> None:
        """Ollama API呼び出しでエラーが発生した場合に、警告を出して空リストを返し、フォールバックすること"""
        mock_post.side_effect = Exception("Connection error")
        from generate_data import generate_user_prompts_ollama
        
        result = generate_user_prompts_ollama(
            "http://localhost:11434", "qwen2.5:7b", 10, ["seed"]
        )
        self.assertEqual(result, [])

    @patch("os.path.exists")
    @patch("evaluate.run_batch")
    @patch("evaluate.run_interactive")
    @patch("mlx_lm.load")
    def test_evaluate_main_batch_mode(
        self, mock_load: MagicMock, mock_interactive: MagicMock, mock_batch: MagicMock, mock_exists: MagicMock
    ) -> None:
        """evaluate.py の main 関数が一括モードで正しく呼び出せること"""
        mock_exists.return_value = True
        mock_load.return_value = (MagicMock(), MagicMock())
        
        # コマンドライン引数をシミュレート
        test_args = ["evaluate.py", "--model", "dummy_model", "--adapter", "dummy_adapter"]
        with patch.object(sys, "argv", test_args):
            evaluate.main()
            
        mock_load.assert_called_once_with("dummy_model", adapter_path="dummy_adapter")
        mock_batch.assert_called_once()
        mock_interactive.assert_not_called()


if __name__ == "__main__":
    unittest.main()

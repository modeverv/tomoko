# short LoRA experiment

Target model: `mlx-community/gemma-4-e2b-it-OptiQ-4bit`.

Purpose: bake Tomoko-style short Japanese voice replies and strict `EMOTION:<label>` output into a small MLX-compatible Gemma E2B model.

The dataset is stored as rendered `text` JSONL, not `messages`, because `mlx_lm` chat datasets do not pass `enable_thinking=False` into Gemma 4's chat template. The samples are rendered with `enable_thinking=False` before training so the adapter learns the non-thinking voice-response lane.

## Result

Recommended runtime path for sub-500ms short reactions is the quantized OptiQ base plus `loras/short/adapters`.

`loras/short/fused_model` is the dequantized fused model because the normal quantized fuse was not format-reliable in evaluation. The unreliable quantized fuse is kept at `loras/short/fused_model_quantized_unreliable` for inspection only.

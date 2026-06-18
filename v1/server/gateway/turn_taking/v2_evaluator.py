from __future__ import annotations

import logging
import math
import os
import re

logger = logging.getLogger(__name__)

# Phase TT-v2.10b: fusion 発火判定の重み。
# scripts/shadow_bench.py --tune（N=120, VAP動的VAD）で決定した値:
# 実効リード615ms / miss 0 / 発話中誤発火 0 / 言いさし発火 0。
# 実機ログ突合（TT-v2.10d）で乖離があれば再チューニングして更新する。
FUSION_W_SEM = float(os.environ.get("FUSION_W_SEM", "0.5"))
FUSION_W_QUIET = float(os.environ.get("FUSION_W_QUIET", "0.1"))
FUSION_W_STABLE = float(os.environ.get("FUSION_W_STABLE", "0.2"))
FUSION_W_VAP = float(os.environ.get("FUSION_W_VAP", "0.1"))
FUSION_THETA = float(os.environ.get("FUSION_THETA", "0.6"))
QUIET_GATE_DB = float(os.environ.get("QUIET_GATE_DB", "-45.0"))


class TranscriptValidity:
    # Whisper etc. common silence hallucinations in Japanese
    HALLUCINATION_PATTERNS = [
        r"視聴ありがとうございました",
        r"チャンネル登録",
        r"ご視聴いただき",
        r"高評価",
        r"サブスクライブ",
        r"おやすみなさい",
        r"ありがとうございました",
    ]

    @classmethod
    def evaluate(cls, text: str) -> bool:
        text_stripped = text.strip()
        if not text_stripped:
            return False

        # Too short noise-like characters
        if len(text_stripped) <= 1 and text_stripped in (
            "っ", "ん", "あ", "え", "う", "お", "い", "。", "、", "？", "?"
        ):
            return False

        # Hallucinations
        for pattern in cls.HALLUCINATION_PATTERNS:
            if re.search(pattern, text_stripped):
                return False

        # Unnatural character repetition (e.g. 5+ repeated characters)
        if re.search(r"(\w)\1{4,}", text_stripped):
            return False

        # 2 or 3 character phrase repetitions
        if len(text_stripped) >= 6:
            for k in range(2, 4):
                for i in range(len(text_stripped) - k * 3):
                    sub = text_stripped[i:i+k]
                    if text_stripped[i+k:i+k*2] == sub and text_stripped[i+k*2:i+k*3] == sub:
                        return False

        return True


class StablePrefixExtractor:
    @classmethod
    def extract(cls, history_texts: list[str], current_text: str) -> str:
        """
        history_texts: List of raw_text from previous revisions in the same turn (oldest first).
        current_text: The current raw_text observed.
        """
        if not history_texts:
            return ""

        prev = history_texts[-1]
        if current_text.startswith(prev):
            return prev

        return cls._lcp(prev, current_text)

    @classmethod
    def split_stable_unstable(cls, history_texts: list[str], current_text: str) -> tuple[str, str]:
        stable = cls.extract(history_texts, current_text)
        tail = current_text[len(stable):]
        return stable, tail

    @classmethod
    def _lcp(cls, s1: str, s2: str) -> str:
        limit = min(len(s1), len(s2))
        for i in range(limit):
            if s1[i] != s2[i]:
                return s1[:i]
        return s1[:limit]


class SemanticFinishJudge:
    # Japanese sentence ending markers
    # NOTE: 単独の き/け/げ/ぜ/し/も/ぞ/さ/わ は語中にも頻出し、STT partial の
    # 末尾欠け（例:「〜なんですけど」→「〜なんですけ」）を文末と誤検出するため含めない。
    FINISH_PATTERNS = [
        r"(です|ます|だ|である|ください|なさい|ね|よ|よね|か|の|かな|な|ねえ|よぉ|でしょ|でしょう)(\.|\?|。|？)?$",
        r"(う|よう|まい|ます|です|だ|だろう|でしょう)(\.|\?|。|？)?$",
        # 丁寧形の過去・否定・勧誘、および促音便の過去形（「〜ました」「〜だった」「〜わかった」）
        r"(ました|でした|ません|ませんでした|ましょう|った)(\.|\?|。|？)?$",
        r"(\?|？|！|!)$",
        r"(思う|思われます|考えます|感じます|知れません|ありません|ございます)(\.|。)?$"
    ]

    # Conjugations or unfinished ending particles
    UNFINISHED_PATTERNS = [
        r"(けど|けれど|が|から|ので|し|て|で|と|たら|なら|ば|ながら|つつ|ため|ものの|けれども|のに|からには|以上は|と同時に|一方|反面)$",
        r"(〜|～|…|\.\.\.)$",
        r"(という|といった|といったような|などの|の様な|のよう|のような)$"
    ]

    # Post-conjunctive split risks (starts continuation)
    SPLIT_RISK_PATTERNS = [
        r"(ただ|ただし|でも|しかし|というか|ていうか|だけど|前提として|一個だけ|一つだけ|ちなみに|なお|かつ|また)$"
    ]

    @classmethod
    def evaluate(cls, text: str) -> dict[str, float | int]:
        text_stripped = text.strip()
        if not text_stripped:
            return {
                "semantic_saturation": 0.0,
                "remaining_info_risk": 1.0,
                "semantic_split_risk": 0.0,
                "safe_response_level": 0,
                "confidence": 0.0
            }

        semantic_saturation = 0.3
        semantic_split_risk = 0.0

        has_finish = False
        for pattern in cls.FINISH_PATTERNS:
            if re.search(pattern, text_stripped):
                has_finish = True
                break

        has_unfinished = False
        for pattern in cls.UNFINISHED_PATTERNS:
            if re.search(pattern, text_stripped):
                has_unfinished = True
                break

        has_split_risk = False
        for pattern in cls.SPLIT_RISK_PATTERNS:
            if re.search(pattern, text_stripped):
                has_split_risk = True
                break

        if has_finish:
            semantic_saturation = 0.85
        if has_unfinished:
            semantic_saturation = 0.15

        if has_split_risk:
            semantic_split_risk = 0.90
            semantic_saturation = min(semantic_saturation, 0.40)

        if text_stripped.endswith(("？", "?")):
            semantic_saturation = 0.95

        confidence = min(len(text_stripped) / 15.0, 1.0) * 0.9
        if has_finish:
            confidence += 0.1
        confidence = min(confidence, 1.0)

        remaining_info_risk = 1.0 - semantic_saturation

        if semantic_saturation >= 0.90:
            safe_response_level = 5
        elif semantic_saturation >= 0.75:
            safe_response_level = 4
        elif semantic_saturation >= 0.50:
            safe_response_level = 3
        elif semantic_saturation >= 0.30:
            safe_response_level = 2
        else:
            safe_response_level = 1

        return {
            "semantic_saturation": round(semantic_saturation, 2),
            "remaining_info_risk": round(remaining_info_risk, 2),
            "semantic_split_risk": round(semantic_split_risk, 2),
            "safe_response_level": safe_response_level,
            "confidence": round(confidence, 2)
        }


class SpeechMotivationEvaluator:
    @classmethod
    def evaluate(
        cls,
        *,
        semantic_saturation: float,
        remaining_info_risk: float,
        semantic_split_risk: float,
        confidence: float,
        vad_state: str | None,
        attention_mode: str | None,
        audio_level_db: float | None,
        p_yielding: float | None = None,
        tail_stable: bool = False,
    ) -> dict[str, float | str]:
        vad_penalty = 0.0
        if vad_state == "listening":
            vad_penalty = 0.8
            if audio_level_db is not None and audio_level_db > -20.0:
                vad_penalty = 0.95

        interruption_risk = remaining_info_risk * 0.7 + semantic_split_risk * 0.3
        understanding = semantic_saturation
        desire = semantic_saturation * 0.9

        # S(t) linear combinations input to sigmoid
        x = (1.5 * desire) + (1.0 * understanding) - (1.5 * interruption_risk) - (3.0 * vad_penalty)
        speech_decision_score = 1.0 / (1.0 + math.exp(-x))

        # Proposals based on scores:
        # S < 0.25 -> silence
        # 0.25 <= S < 0.45 -> prepare_only
        # 0.45 <= S < 0.60 -> backchannel
        # 0.60 <= S < 0.75 -> short_confirmation
        # 0.75 <= S < 0.90 -> full_response_candidate
        # 0.90 <= S -> floor_grab_candidate
        if speech_decision_score < 0.25:
            proposal = "silence"
        elif speech_decision_score < 0.45:
            proposal = "prepare_only"
        elif speech_decision_score < 0.60:
            proposal = "backchannel"
        elif speech_decision_score < 0.75:
            proposal = "short_confirmation"
        elif speech_decision_score < 0.90:
            proposal = "full_response_candidate"
        else:
            proposal = "floor_grab_candidate"

        # Phase TT-v2.3: would_start_inference flag
        # Inference is "ready" when semantic saturation is high, confidence is sufficient,
        # split risk is low, and VAD doesn't indicate active speech.
        would_start_inference = False
        if (
            semantic_saturation >= 0.75
            and confidence >= 0.5
            and semantic_split_risk < 0.5
            and vad_penalty < 0.5
        ):
            would_start_inference = True

        # Phase TT-v2.10b: fusion 発火判定（log-only 並走。provisional inference の
        # トリガーは従来の would_start_inference のまま）。
        # listening 中でも「音声エネルギーが静か + テキスト確定 + 意味完了 + VAP高確度」の
        # 加重和がしきい値を超えれば発火可とみなす。
        quiet = vad_state != "listening" or (
            audio_level_db is not None and audio_level_db <= QUIET_GATE_DB
        )
        sem_core = semantic_saturation * confidence * (1.0 - semantic_split_risk)
        fusion_score = (
            FUSION_W_SEM * sem_core
            + FUSION_W_QUIET * (1.0 if quiet else 0.0)
            + FUSION_W_STABLE * (1.0 if tail_stable else 0.0)
            + FUSION_W_VAP * (p_yielding or 0.0)
        )
        would_start_inference_fusion = fusion_score >= FUSION_THETA

        return {
            "speech_decision_score": round(speech_decision_score, 3),
            "proposal": proposal,
            "would_start_inference": would_start_inference,
            "fusion_score": round(fusion_score, 3),
            "would_start_inference_fusion": would_start_inference_fusion,
        }

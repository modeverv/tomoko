from __future__ import annotations

EMOTION_TO_IMAGE = {
    "neutral": "/assets/images/tomoko-neutral.svg",
    "happy": "/assets/images/tomoko-happy.svg",
    "surprised": "/assets/images/tomoko-surprised.svg",
    "sad": "/assets/images/tomoko-sad.svg",
    "thinking": "/assets/images/tomoko-thinking.svg",
    "gentle": "/assets/images/tomoko-gentle.svg",
    "excited": "/assets/images/tomoko-excited.svg",
}


class EmotionImageMapper:
    """Maps reply emotion values to server-owned image asset paths."""

    def image_for(self, emotion: str) -> str:
        return EMOTION_TO_IMAGE.get(emotion, EMOTION_TO_IMAGE["neutral"])

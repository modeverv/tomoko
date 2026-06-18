from __future__ import annotations

from dataclasses import dataclass

EMOTION_TO_IMAGE = {
    "neutral": "/assets/images/tomoko-neutral.svg",
    "happy": "/assets/images/tomoko-happy.svg",
    "surprised": "/assets/images/tomoko-surprised.svg",
    "sad": "/assets/images/tomoko-sad.svg",
    "thinking": "/assets/images/tomoko-thinking.svg",
    "gentle": "/assets/images/tomoko-gentle.svg",
    "excited": "/assets/images/tomoko-excited.svg",
}


@dataclass(frozen=True)
class ReplyDisplayState:
    emotion: str
    image: str


class ReplyDisplayPlanner:
    """Owns reply display state derived from thinking events."""

    def __init__(self, *, initial_emotion: str = "neutral") -> None:
        self.current_emotion = initial_emotion

    def update_emotion(self, emotion: str) -> ReplyDisplayState:
        self.current_emotion = emotion
        return ReplyDisplayState(
            emotion=emotion,
            image=self._image_for(emotion),
        )

    def _image_for(self, emotion: str) -> str:
        return EMOTION_TO_IMAGE.get(emotion, EMOTION_TO_IMAGE["neutral"])

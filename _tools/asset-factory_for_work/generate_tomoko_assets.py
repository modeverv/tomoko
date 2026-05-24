from __future__ import annotations

# ruff: noqa: E501
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "assets" / "images"


@dataclass(frozen=True)
class PortraitSpec:
    emotion: str
    bg: str
    accent: str
    mouth: str
    eye_left: str
    eye_right: str
    brow_left: str
    brow_right: str
    cheek_opacity: str


SPECS = [
    PortraitSpec("neutral", "#dfe9e7", "#315f68", "M141 205 Q160 214 179 205", "M124 157 Q132 151 140 157", "M180 157 Q188 151 196 157", "M119 139 L143 136", "M177 136 L201 139", "0.22"),
    PortraitSpec("happy", "#fff1d7", "#c77d45", "M134 201 Q160 229 186 201", "M122 154 Q132 166 142 154", "M178 154 Q188 166 198 154", "M119 137 L143 132", "M177 132 L201 137", "0.42"),
    PortraitSpec("surprised", "#e6f2ff", "#547aa5", "M151 203 Q160 193 169 203 Q160 219 151 203", "M125 156 A9 11 0 1 0 126 156", "M185 156 A9 11 0 1 0 186 156", "M119 132 L143 127", "M177 127 L201 132", "0.28"),
    PortraitSpec("sad", "#e8e2ee", "#735f84", "M140 213 Q160 199 180 213", "M124 160 Q132 154 140 160", "M180 160 Q188 154 196 160", "M119 132 L143 141", "M177 141 L201 132", "0.16"),
    PortraitSpec("thinking", "#ece7dc", "#6a6258", "M144 205 Q160 211 176 205", "M124 157 Q132 151 140 157", "M180 157 Q188 151 196 157", "M119 134 L143 137", "M177 137 L201 134", "0.18"),
    PortraitSpec("gentle", "#e8f1e5", "#5f8065", "M139 203 Q160 220 181 203", "M123 156 Q132 162 141 156", "M179 156 Q188 162 197 156", "M119 138 L143 136", "M177 136 L201 138", "0.32"),
    PortraitSpec("excited", "#ffe5e2", "#d05f54", "M132 199 Q160 230 188 199", "M122 153 Q132 166 142 153", "M178 153 Q188 166 198 153", "M119 134 L143 128", "M177 128 L201 134", "0.50"),
]


def build_svg(spec: PortraitSpec) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 380" role="img" aria-label="Tomoko {spec.emotion}">
  <rect width="320" height="380" fill="{spec.bg}"/>
  <path d="M64 350 C77 292 91 251 112 226 H208 C229 251 243 292 256 350 Z" fill="#40515f"/>
  <path d="M101 350 C111 299 126 270 160 270 C194 270 209 299 219 350 Z" fill="{spec.accent}"/>
  <path d="M91 153 C91 92 119 55 160 55 C201 55 229 92 229 153 C229 218 202 253 160 253 C118 253 91 218 91 153 Z" fill="#f4c9ad"/>
  <path d="M82 151 C84 88 113 43 160 43 C207 43 236 88 238 151 C223 120 198 105 160 105 C122 105 97 120 82 151 Z" fill="#313845"/>
  <path d="M94 121 C116 78 145 67 160 67 C175 67 204 78 226 121 C212 91 189 78 160 78 C131 78 108 91 94 121 Z" fill="#46505e"/>
  <path d="{spec.brow_left}" stroke="#313845" stroke-width="5" stroke-linecap="round"/>
  <path d="{spec.brow_right}" stroke="#313845" stroke-width="5" stroke-linecap="round"/>
  <path d="{spec.eye_left}" stroke="#2b2f36" stroke-width="6" stroke-linecap="round" fill="none"/>
  <path d="{spec.eye_right}" stroke="#2b2f36" stroke-width="6" stroke-linecap="round" fill="none"/>
  <path d="M159 160 C154 177 151 185 160 188" stroke="#d99d86" stroke-width="4" stroke-linecap="round" fill="none"/>
  <path d="{spec.mouth}" stroke="#8f4b4b" stroke-width="6" stroke-linecap="round" fill="none"/>
  <ellipse cx="119" cy="186" rx="20" ry="11" fill="#ee8f93" opacity="{spec.cheek_opacity}"/>
  <ellipse cx="201" cy="186" rx="20" ry="11" fill="#ee8f93" opacity="{spec.cheek_opacity}"/>
  <path d="M82 151 C73 183 77 221 104 243" stroke="#313845" stroke-width="18" stroke-linecap="round" fill="none"/>
  <path d="M238 151 C247 183 243 221 216 243" stroke="#313845" stroke-width="18" stroke-linecap="round" fill="none"/>
  <path d="M115 274 C132 286 188 286 205 274" stroke="#f4c9ad" stroke-width="18" stroke-linecap="round"/>
</svg>
"""


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in SPECS:
        (OUT_DIR / f"tomoko-{spec.emotion}.svg").write_text(
            build_svg(spec),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()

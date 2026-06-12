from __future__ import annotations

import abc
from collections.abc import AsyncGenerator


class InferenceBackend(abc.ABC):
    """推論バックエンドの基底。

    Optional メソッド（Phase TT-v2.10c、対応バックエンドのみ実装）:
      - ``async def prefill(cache_key, system_prompt, messages) -> dict``
        プロンプトの KV キャッシュを構築/延長する（生成はしない）。
      - ``async def drop_prefill(cache_key) -> None``
        キャッシュを破棄する（ターン終了時に呼ぶ）。
    呼び出し側は必ず ``getattr(backend, "prefill", None)`` で存在確認すること
    （未実装バックエンドでは no-op 扱い）。KV キャッシュはバックエンド内に閉じ、
    ターン内 append-only・ターン終了で破棄の規約とする。
    """

    name: str
    privacy_allowed: bool

    @abc.abstractmethod
    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        trace_role: str | None = None,
    ) -> AsyncGenerator[str, None]:
        del trace_role
        if False:
            yield ""

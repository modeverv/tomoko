from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from server.shared.models import AudioChunkOut, ModelOutputEvent, PromptRequest, PromptScope


class ChatBackend:
    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        raise NotImplementedError


class TtsBackend:
    async def synthesize_chunks(self, request: PromptRequest, text: str) -> AsyncIterator[bytes]:
        raise NotImplementedError


class StaticChatBackend(ChatBackend):
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        for delta in self._deltas:
            yield delta


class StaticWavTtsBackend(TtsBackend):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def synthesize_chunks(self, request: PromptRequest, text: str) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class OpenAICompatibleChatBackend(ChatBackend):
    def __init__(
        self,
        *,
        url: str,
        model: str,
        max_tokens: int = 180,
        temperature: float = 0.0,
        chat_template_kwargs: dict[str, Any] | None = None,
        timeout_sec: float = 60.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.chat_template_kwargs = dict(chat_template_kwargs or {})
        self.timeout_sec = timeout_sec

    async def stream(self, request: PromptRequest) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _messages_for_request(request),
            "stream": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.chat_template_kwargs:
            payload["chat_template_kwargs"] = dict(self.chat_template_kwargs)
        timeout = httpx.Timeout(self.timeout_sec, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    content = parse_openai_sse_content(line)
                    if content:
                        yield content


class VoicevoxChunkedTtsBackend(TtsBackend):
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:50122",
        speaker_id: int = 8,
        sample_rate: int = 24000,
        speed: float = 1.5,
        chunk_min_accent_phrases: int = 1,
        segment_length: float = 0.6,
        timeout_sec: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.speaker_id = speaker_id
        self.sample_rate = sample_rate
        self.speed = speed
        self.chunk_min_accent_phrases = chunk_min_accent_phrases
        self.segment_length = segment_length
        self.timeout_sec = timeout_sec

    async def synthesize_chunks(self, request: PromptRequest, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        timeout = httpx.Timeout(self.timeout_sec, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            audio_query = await self._audio_query(client, text)
            async with client.stream(
                "POST",
                f"{self.url}/streaming_synthesis",
                params={
                    "speaker": self.speaker_id,
                    "chunk_min_accent_phrases": self.chunk_min_accent_phrases,
                    "segment_length": self.segment_length,
                },
                json=audio_query,
            ) as response:
                response.raise_for_status()
                async for chunk in iter_voicevox_streaming_chunks(response):
                    if not is_complete_wav_chunk(chunk):
                        raise ValueError("VOICEVOX chunk must be a complete WAV")
                    yield chunk

    async def _audio_query(self, client: httpx.AsyncClient, text: str) -> dict[str, Any]:
        response = await client.post(
            f"{self.url}/audio_query",
            params={"text": text, "speaker": self.speaker_id},
        )
        response.raise_for_status()
        audio_query = response.json()
        audio_query["speedScale"] = self.speed
        audio_query["outputSamplingRate"] = self.sample_rate
        audio_query["outputStereo"] = False
        return audio_query


def _messages_for_request(request: PromptRequest) -> list[dict[str, str]]:
    transcript_messages = _session_transcript_messages(request.prompt_text)
    if transcript_messages is not None:
        return transcript_messages
    if request.scope == PromptScope.SHORT:
        system = "EMOTION:<label> の1行と、短い日本語1文だけを返す。"
    else:
        system = "あなたはTTSで自然に読める日本語だけで返す。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": request.prompt_text},
    ]


def _session_transcript_messages(prompt_text: str) -> list[dict[str, str]] | None:
    if "SESSION_TRANSCRIPT:" not in prompt_text or "SYSTEM:" not in prompt_text:
        return None
    system_body = _section_between(prompt_text, "SYSTEM:", "INSTRUCTION:")
    instruction_body = _section_between(prompt_text, "INSTRUCTION:", "SESSION_TRANSCRIPT:")
    transcript_body = prompt_text.split("SESSION_TRANSCRIPT:", 1)[1].strip()
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n".join(
                part.strip() for part in (system_body, instruction_body) if part.strip()
            ),
        }
    ]
    for line in transcript_body.splitlines():
        if line.startswith("user: "):
            messages.append({"role": "user", "content": line.removeprefix("user: ")})
        elif line.startswith("tomoko: "):
            messages.append(
                {"role": "assistant", "content": line.removeprefix("tomoko: ")}
            )
    if len(messages) <= 1 or messages[-1]["role"] != "user":
        return None
    return messages


def _section_between(text: str, start_marker: str, end_marker: str) -> str:
    if start_marker not in text:
        return ""
    tail = text.split(start_marker, 1)[1]
    if end_marker in tail:
        return tail.split(end_marker, 1)[0].strip()
    return tail.strip()


def parse_openai_sse_content(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == "[DONE]":
        return None
    payload = json.loads(data)
    choices = payload.get("choices")
    if not choices:
        return None
    delta = choices[0].get("delta", {})
    return delta.get("content")


async def iter_voicevox_streaming_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    content_type = response.headers.get("content-type", "")
    boundary = parse_multipart_boundary(content_type)
    if boundary is not None:
        parser = MultipartMixedParser(boundary)
        async for data in response.aiter_bytes():
            for chunk in parser.feed(data):
                yield chunk
        for chunk in parser.finish():
            yield chunk
        return
    if content_type.lower().partition(";")[0].strip() == "audio/wav":
        data = b""
        async for part in response.aiter_bytes():
            data += part
        yield data
        return
    raise ValueError(f"unsupported VOICEVOX streaming content-type: {content_type!r}")


def parse_multipart_boundary(content_type: str) -> str | None:
    for part in content_type.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "boundary":
            return value.strip('"')
    return None


class MultipartMixedParser:
    def __init__(self, boundary: str) -> None:
        self._boundary = f"--{boundary}\r\n".encode("ascii")
        self._closing_boundary = f"--{boundary}--\r\n".encode("ascii")
        self._buffer = bytearray()
        self._closed = False

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        return self._parse_available()

    def finish(self) -> list[bytes]:
        chunks = self._parse_available()
        if self._buffer and not self._closed:
            raise ValueError("incomplete multipart VOICEVOX response")
        return chunks

    def _parse_available(self) -> list[bytes]:
        chunks: list[bytes] = []
        while True:
            if self._closed:
                return chunks
            if self._buffer.startswith(self._closing_boundary):
                del self._buffer[: len(self._closing_boundary)]
                self._closed = True
                return chunks
            if not self._buffer.startswith(self._boundary):
                return chunks
            header_start = len(self._boundary)
            header_end = self._buffer.find(b"\r\n\r\n", header_start)
            if header_end < 0:
                return chunks
            headers = _parse_multipart_headers(bytes(self._buffer[header_start:header_end]))
            body_start = header_end + 4
            body_length = int(headers["content-length"])
            body_end = body_start + body_length
            part_end = body_end + 2
            if len(self._buffer) < part_end:
                return chunks
            body = bytes(self._buffer[body_start:body_end])
            if self._buffer[body_end:part_end] != b"\r\n":
                raise ValueError("multipart part is not followed by CRLF")
            del self._buffer[:part_end]
            chunks.append(body)


def _parse_multipart_headers(header_bytes: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_bytes.decode("ascii").split("\r\n"):
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"invalid multipart header: {line!r}")
        headers[key.lower()] = value.strip()
    return headers


def is_complete_wav_chunk(chunk: bytes) -> bool:
    return len(chunk) >= 12 and chunk[:4] == b"RIFF" and chunk[8:12] == b"WAVE"


@dataclass(slots=True)
class PromptExecutionResult:
    model_events: list[ModelOutputEvent] = field(default_factory=list)
    audio_chunks: list[AudioChunkOut] = field(default_factory=list)


class PromptExecutor:
    def __init__(self, chat_backend: ChatBackend, tts_backend: TtsBackend) -> None:
        self._chat_backend = chat_backend
        self._tts_backend = tts_backend

    async def execute(self, request: PromptRequest) -> PromptExecutionResult:
        result = PromptExecutionResult()
        text_parts: list[str] = []
        _console_prompt(request)
        async for delta in self._chat_backend.stream(request):
            text_parts.append(delta)
            result.model_events.append(
                ModelOutputEvent(
                    request_id=request.id,
                    event_kind="delta",
                    text_delta=delta,
                    trace_id=request.trace_id,
                )
            )
        full_text = "".join(text_parts)
        result.model_events.append(
            ModelOutputEvent(
                request_id=request.id,
                event_kind="complete",
                text=full_text,
                trace_id=request.trace_id,
            )
        )
        async for chunk in self._tts_backend.synthesize_chunks(request, full_text):
            if not is_complete_wav_chunk(chunk):
                raise ValueError("TTS backend must yield complete WAV chunks")
            result.audio_chunks.append(
                AudioChunkOut(
                    request_id=request.id,
                    chunk=chunk,
                    sample_rate=16000,
                    trace_id=request.trace_id,
                )
            )
        if result.audio_chunks:
            result.audio_chunks[-1].is_final = True
        return result


def create_default_real_prompt_executor() -> PromptExecutor:
    return PromptExecutor(
        OpenAICompatibleChatBackend(
            url=os.environ.get("TOMOKO_V2_LLM_URL", "http://127.0.0.1:8082"),
            model=os.environ.get("TOMOKO_V2_LLM_MODEL", "gemma-4-26b-a4b-it-mlx"),
            max_tokens=int(os.environ.get("TOMOKO_V2_LLM_MAX_TOKENS", "180")),
            chat_template_kwargs={"enable_thinking": False},
        ),
        VoicevoxChunkedTtsBackend(
            url=os.environ.get("TOMOKO_V2_VOICEVOX_URL", "http://127.0.0.1:50122"),
            speaker_id=int(os.environ.get("TOMOKO_V2_VOICEVOX_SPEAKER", "8")),
            sample_rate=int(os.environ.get("TOMOKO_V2_VOICEVOX_SAMPLE_RATE", "24000")),
            speed=float(os.environ.get("TOMOKO_V2_VOICEVOX_SPEED", "1.5")),
            segment_length=float(os.environ.get("TOMOKO_V2_VOICEVOX_SEGMENT_LENGTH", "0.6")),
        ),
    )


def _console_prompt(request: PromptRequest) -> None:
    print(
        "[tomoko:llm] prompt_send "
        f"request_id={str(request.id)!r} scope={request.scope.value!r} "
        f"chars={len(request.prompt_text)!r}",
        flush=True,
    )
    print("----- TOMOKO LLM PROMPT BEGIN -----", flush=True)
    print(request.prompt_text, flush=True)
    print("----- TOMOKO LLM PROMPT END -----", flush=True)

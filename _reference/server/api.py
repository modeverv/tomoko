import base64
import io
import os
# from multiprocessing import Queue
from typing import Dict

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydub import AudioSegment

from libs.chats import ManagerProcess
from libs.my_inputs import AudioInput
from libs.my_responses import RootResponse, CanStartChat, TextResponse,  MakeChatRequest
from libs.voice_api_layer import VoiceAPILayer

load_dotenv()
LIMIT = int(os.environ.get("CHAT_LIMIT"))

app = FastAPI()


# queue = Queue()
# manager = ManagerProcess(queue)
# manager.start()
# key is roomid + _ + user_id
chats: Dict[str, VoiceAPILayer] = {}

@app.get("/", response_model=RootResponse)
async def read_root():
    return {"server": "ai-host-api-server"}


@app.get("/can_start_chat", response_model=CanStartChat)
async def can_start_chat():
    l = len(chats)
    if l > LIMIT:
        return {"result": "false"}
    else:
        return {"result": "true"}


# make chat
@app.post("/make_chat", response_model=CanStartChat)
async def make_chat(req: MakeChatRequest):
    try:
        print(_get_chat_key(
            req.room_id,
            req.user_id
        ))
        chat = VoiceAPILayer(False)
        chat.multi_set(
            req.lang,
            req.type_text,
            req.chat_kind,
            req.json_str,
            req.ai_engine
        )
        chat.init_chat()

        chats[_get_chat_key(
            req.room_id,
            req.user_id)
        ] = chat
        return {"result": "true"}
    except Exception as e:
        print(e)
        return {"result": "false"}


# first, get text from audio
@app.post("/get_text_from_audio")
async def get_text_from_audio(audio_input: AudioInput):
    key = _get_chat_key(audio_input.roomid, audio_input.user_id)
    if key not in chats:
        return "chat not found"
    try:
        ogg_binary = base64.b64decode(audio_input.audio_base64)
        ogg_bytes_io = io.BytesIO(ogg_binary)
        mp3_bytes_io = convert_ogg_to_mp3(ogg_bytes_io)
    except Exception as e:
        return {"result": "failed", "text": e}
    chat = chats[_get_chat_key(audio_input.roomid, audio_input.user_id)]
    text = chat.get_text_from_whisper(mp3_bytes_io, key)
    return {"result": "success", "text": text}


def convert_ogg_to_mp3(ogg_bytes_io):
    # Load ogg from BytesIO
    ogg_audio = AudioSegment.from_ogg(ogg_bytes_io)
    # Create BytesIO for mp3
    mp3_bytes_io = io.BytesIO()
    # Export as mp3 to BytesIO
    ogg_audio.export(mp3_bytes_io, format="mp3")
    # Seek to start of BytesIO
    mp3_bytes_io.seek(0)
    return mp3_bytes_io


# second, get wave and text response from text
@app.get("/get_response_wave_from_text")
async def get_response_wave_from_text(roomid: str, user_id: str, text: str):
    key = _get_chat_key(roomid, user_id)
    if key not in chats:
        return "chat not found"
    chat = chats[_get_chat_key(roomid, user_id)]
    dto = chat.get_wave_from_text(text)
    return dto.get_serialized_and_remove_audio_file()


# third, finish chat and get memory
@app.get("/finish_chat")
async def finish_chat(roomid: str, user_id: str):
    key = _get_chat_key(roomid, user_id)
    if key not in chats:
        return []
    chat = chats[_get_chat_key(roomid, user_id)]
    memory = chat.get_memory()
    chats.pop(key)
    return memory


def _get_chat_key(room_id: str, user_id: str):
    return room_id + "_" + user_id

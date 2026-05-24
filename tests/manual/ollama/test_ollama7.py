from ollama._types import ChatResponse
from pydantic import BaseModel

print(issubclass(ChatResponse, BaseModel))

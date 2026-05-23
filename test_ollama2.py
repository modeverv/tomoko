import asyncio
import inspect
from ollama import AsyncClient

async def main():
    print(inspect.signature(AsyncClient.chat))

asyncio.run(main())

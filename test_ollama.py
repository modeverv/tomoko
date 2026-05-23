import asyncio
from ollama import AsyncClient

async def main():
    print(getattr(AsyncClient, "chat"))

asyncio.run(main())

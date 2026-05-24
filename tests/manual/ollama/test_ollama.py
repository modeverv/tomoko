import asyncio

from ollama import AsyncClient


async def main():
    print(AsyncClient.chat)

asyncio.run(main())

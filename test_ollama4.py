import asyncio
from ollama import AsyncClient

async def main():
    try:
        # Just check what part is, without requiring a real model or with a mock
        pass
    except Exception as e:
        print("Error", e)

asyncio.run(main())

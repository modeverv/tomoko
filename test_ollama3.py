import asyncio

from ollama import AsyncClient


async def main():
    try:
        gen = await AsyncClient().chat(model="nonexistent", stream=True)
        print("awaited successfully", type(gen))
    except Exception as e:
        print("Error awaiting:", e)
    
    try:
        gen2 = AsyncClient().chat(model="nonexistent", stream=True)
        print("called without await:", type(gen2))
    except Exception as e:
        print("Error not awaiting:", e)

asyncio.run(main())

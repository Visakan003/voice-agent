import asyncio
import aiohttp
import time

URL = "https://voice-agent-g7uwbzqq.livekit.cloud"

async def test():
    start = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(URL) as resp:
            print("Status:", resp.status)
    print("Time:", round(time.time() - start, 2), "seconds")

asyncio.run(test())
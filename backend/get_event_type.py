import asyncio
import os

import aiohttp
from dotenv import load_dotenv


load_dotenv()


async def main() -> None:
    token = (os.getenv("CALENDLY_ACCESS_TOKEN", "") or "").strip()
    if not token:
        raise RuntimeError("Missing CALENDLY_ACCESS_TOKEN in environment/.env")

    async with aiohttp.ClientSession() as session:
        # First get your user URI
        async with session.get(
            "https://api.calendly.com/users/me",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            user = await resp.json()
            user_uri = user["resource"]["uri"]
            print(f"User URI: {user_uri}")

        # Then get your event types
        async with session.get(
            "https://api.calendly.com/event_types",
            params={"user": user_uri},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            data = await resp.json()
            for et in data.get("collection", []):
                print(f"\nName: {et['name']}")
                print(f"URI:  {et['uri']}")
                print(f"Active: {et['active']}")


if __name__ == "__main__":
    asyncio.run(main())


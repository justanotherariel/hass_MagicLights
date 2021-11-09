import aiofiles
import json
import os


async def load_presets() -> dict:
    async with aiofiles.open(
        os.path.join(os.path.dirname(__file__), "../values.json"), mode="r"
    ) as f:
        values = await f.read()

    return json.loads(values)

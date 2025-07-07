import asyncio


async def run_sleeping_task(event):
    # Replace with real logic; here we just succeed
    await asyncio.sleep(60)
    return True

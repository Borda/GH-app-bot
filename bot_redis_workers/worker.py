import asyncio
import json
import logging
import traceback

import redis

from bot_redis_workers import REDIS_QUEUE, REDIS_URL
from bot_redis_workers.tasks import process_task

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logging.info("Starting worker...")

    # Connect to Redis (shared across workers)
    redis_client = redis.from_url(REDIS_URL)

    while True:
        _, task_json = redis_client.blpop(REDIS_QUEUE)
        task = json.loads(task_json)
        try:
            asyncio.run(process_task(task, redis_client))
        except KeyboardInterrupt:
            redis_client.rpush(REDIS_QUEUE, json.dumps(task))
            break
        except Exception as ex:
            # with {json.dumps(task, indent=4, sort_keys=True)}
            logging.error(f"Error processing task: \n\t{ex!r}\n{traceback.format_exc()}")
            redis_client.rpush(REDIS_QUEUE, json.dumps(task))
        else:
            logging.debug(f"Successfully processed task: {task}")

    logging.info("Worker stopped.")

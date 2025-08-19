import json
import logging

import redis

from bot_redis_workers import REDIS_URL, REDIS_QUEUE
from bot_redis_workers.tasks import process_task

if __name__ == "__main__":
    logging.info("Starting worker...")

    # Connect to Redis (shared across workers)
    redis_client = redis.from_url(REDIS_URL)

    while True:
        _, task_json = redis_client.blpop(REDIS_QUEUE)
        task = json.loads(task_json)
        try:
            process_task(task, redis_client)
        except KeyboardInterrupt:
            break
        except Exception as ex:
            logging.error(f"Error processing task {json.dumps(task, indent=4, sort_keys=True)}: {ex}")
        else:
            logging.debug(f"Successfully processed task: {task}")

    logging.info("Worker stopped.")
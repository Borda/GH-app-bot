import json

import redis

from bot_redis_workers import REDIS_URL
from bot_redis_workers.tasks import process_task

redis_client = redis.from_url(REDIS_URL)

if __name__ == "__main__":
    print("Starting worker...")
    while True:
        _, task_json = redis_client.blpop("bot_queue")
        task = json.loads(task_json)
        process_task(task)

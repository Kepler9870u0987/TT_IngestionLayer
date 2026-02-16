#!/usr/bin/env python3
"""Restore Redis data from a JSON backup."""
import argparse
import json
from pathlib import Path
from typing import Any, Dict

from config.settings import Settings, settings
from src.common.logging_config import get_logger
from src.common.redis_client import RedisClient

logger = get_logger(__name__)


def restore_key(client: RedisClient, key: str, payload: Dict[str, Any]) -> None:
    key_type = payload.get("type")
    value = payload.get("value")
    client.client.delete(key)

    if key_type == "string":
        client.client.set(key, value)
    elif key_type == "list":
        if value:
            client.client.rpush(key, *value)
    elif key_type == "set":
        if value:
            client.client.sadd(key, *value)
    elif key_type == "zset":
        if value:
            mapping = {member: float(score) for member, score in value}
            client.client.zadd(key, mapping)
    elif key_type == "hash":
        if value:
            client.client.hset(key, mapping=value)
    elif key_type == "stream":
        for entry in value:
            client.client.xadd(key, entry.get("fields", {}), id=entry.get("id"))
    else:
        logger.warning(f"Unsupported type '{key_type}' for key '{key}' - skipping")


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore Redis from JSON backup")
    parser.add_argument("--file", required=True, help="Path to backup JSON file")
    parser.add_argument("--db", type=int, help="Redis database", default=None)
    args = parser.parse_args()

    backup_path = Path(args.file)
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    cfg: Settings = settings or Settings()
    redis_db = args.db if args.db is not None else cfg.redis.db

    client = RedisClient(host=cfg.redis.host, port=cfg.redis.port, password=cfg.redis.password, db=redis_db)
    logger.info(f"Restoring backup into Redis db={redis_db} from {backup_path}")

    payload = json.loads(backup_path.read_text(encoding="utf-8"))
    data: Dict[str, Any] = payload.get("data", {})

    for key, value in data.items():
        restore_key(client, key, value)

    logger.info(f"Restore completed: {len(data)} keys restored")


if __name__ == "__main__":
    main()

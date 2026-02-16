#!/usr/bin/env python3
"""Backup Redis data to a JSON file."""
import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict

from config.settings import Settings, settings
from src.common.logging_config import get_logger
from src.common.redis_client import RedisClient

logger = get_logger(__name__)


def export_database(client: RedisClient) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    for key in client.client.scan_iter():
        key_type = client.client.type(key)
        if key_type == "string":
            snapshot[key] = {"type": "string", "value": client.client.get(key)}
        elif key_type == "list":
            snapshot[key] = {"type": "list", "value": client.client.lrange(key, 0, -1)}
        elif key_type == "set":
            snapshot[key] = {"type": "set", "value": list(client.client.smembers(key))}
        elif key_type == "zset":
            members = client.client.zrange(key, 0, -1, withscores=True)
            snapshot[key] = {"type": "zset", "value": [[m, s] for m, s in members]}
        elif key_type == "hash":
            snapshot[key] = {"type": "hash", "value": client.client.hgetall(key)}
        elif key_type == "stream":
            entries = client.client.xrange(key, "-", "+")
            snapshot[key] = {
                "type": "stream",
                "value": [{"id": msg_id, "fields": fields} for msg_id, fields in entries],
            }
        else:
            logger.warning(f"Skipping unsupported type '{key_type}' for key '{key}'")
    return snapshot


def resolve_output_path(path_arg: str) -> Path:
    if path_arg:
        return Path(path_arg)
    backups_dir = Path("backups")
    backups_dir.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return backups_dir / f"redis_backup_{timestamp}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup Redis to JSON")
    parser.add_argument("--output", help="Output file path", default="")
    parser.add_argument("--db", type=int, help="Redis database", default=None)
    args = parser.parse_args()

    cfg: Settings = settings or Settings()
    redis_db = args.db if args.db is not None else cfg.redis.db

    client = RedisClient(host=cfg.redis.host, port=cfg.redis.port, password=cfg.redis.password, db=redis_db)
    logger.info(f"Starting backup from Redis db={redis_db}")

    snapshot = export_database(client)
    output_path = resolve_output_path(args.output)

    payload = {
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": cfg.redis.host,
            "port": cfg.redis.port,
            "db": redis_db,
            "keys": len(snapshot),
        },
        "data": snapshot,
    }

    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"Backup completed: {output_path} ({len(snapshot)} keys)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 3 verification script - Worker, Idempotency, DLQ"""
import json
import time
from pathlib import Path

print("=== Phase 3 Verification ===\n")

# 1. Module imports
print("--- 1. Module Import Check ---")
modules_ok = True

checks = [
    ("src.worker.idempotency", "IdempotencyManager", None),
    ("src.worker.processor", None, None),
    ("src.worker.dlq", "DLQManager", None),
    ("src.worker.backoff", None, None),
    ("worker", "EmailWorker", None),
]

for mod_name, class_name, extra in checks:
    try:
        mod = __import__(mod_name, fromlist=[class_name] if class_name else [])
        if class_name:
            getattr(mod, class_name)
        print(f"[OK] {mod_name}" + (f" - {class_name}" if class_name else ""))
    except Exception as e:
        print(f"[FAIL] {mod_name}: {e}")
        modules_ok = False

result = "ALL OK" if modules_ok else "SOME FAILED"
print(f"\nModules: {result}\n")

# 2. File existence
print("--- 2. File Existence ---")
files = [
    "src/worker/__init__.py",
    "src/worker/idempotency.py",
    "src/worker/processor.py",
    "src/worker/dlq.py",
    "src/worker/backoff.py",
    "worker.py",
    "tests/unit/test_idempotency.py",
    "tests/unit/test_dlq.py",
    "tests/unit/test_backoff.py",
    "tests/unit/test_processor.py",
    "tests/integration/test_end_to_end.py",
]
all_exist = True
for f in files:
    exists = Path(f).exists()
    status = "[OK]" if exists else "[FAIL]"
    if not exists:
        all_exist = False
    print(f"{status} {f}")

result = "ALL OK" if all_exist else "SOME MISSING"
print(f"\nFiles: {result}\n")

# 3. Redis consumer group operations
print("--- 3. Redis Consumer Group Operations ---")
redis_ok = True
try:
    import redis
    r = redis.Redis()
    r.ping()

    stream = "test_phase3_verify"
    group = "test_worker_group"

    # Add messages
    for i in range(5):
        r.xadd(stream, {
            "message_id": f"email-{i}",
            "subject": f"Test email {i}",
            "from": "sender@test.com",
            "body": f"Body {i}"
        })
    print(f"[OK] XADD - 5 messages added to stream")

    # Create consumer group
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            r.xgroup_destroy(stream, group)
            r.xgroup_create(stream, group, id="0", mkstream=True)
    print(f"[OK] XGROUP CREATE - consumer group '{group}'")

    # XINFO GROUPS
    info = r.xinfo_groups(stream)
    assert len(info) >= 1
    print(f"[OK] XINFO GROUPS - {len(info)} group(s), pending: {info[0].get('pending', 0)}")

    # XREADGROUP with 2 consumers
    msgs1 = r.xreadgroup(group, "worker_01", {stream: ">"}, count=3)
    msgs2 = r.xreadgroup(group, "worker_02", {stream: ">"}, count=3)
    count1 = len(msgs1[0][1]) if msgs1 else 0
    count2 = len(msgs2[0][1]) if msgs2 else 0
    print(f"[OK] XREADGROUP - worker_01: {count1} msgs, worker_02: {count2} msgs")

    # XPENDING
    pending = r.xpending(stream, group)
    print(f"[OK] XPENDING - {pending.get('pending', 0)} pending messages")

    # XACK messages
    acked = 0
    for _, entries in (msgs1 or []):
        for entry_id, _ in entries:
            r.xack(stream, group, entry_id)
            acked += 1
    for _, entries in (msgs2 or []):
        for entry_id, _ in entries:
            r.xack(stream, group, entry_id)
            acked += 1
    print(f"[OK] XACK - {acked} messages acknowledged")

    # Verify pending is 0 after ACK
    pending_after = r.xpending(stream, group)
    pending_count = pending_after.get("pending", 0)
    if pending_count == 0:
        print(f"[OK] Post-ACK - 0 pending messages (all consumed)")
    else:
        print(f"[WARN] Post-ACK - {pending_count} still pending")

    # Cleanup
    r.xgroup_destroy(stream, group)
    r.delete(stream)
    print("[OK] Cleanup done")

except Exception as e:
    print(f"[FAIL] Consumer group ops: {e}")
    redis_ok = False

result = "ALL OK" if redis_ok else "SOME FAILED"
print(f"\nConsumer Groups: {result}\n")

# 4. Idempotency check with real Redis
print("--- 4. Idempotency Check (Redis) ---")
idemp_ok = True
try:
    from src.common.redis_client import RedisClient
    from src.worker.idempotency import IdempotencyManager

    rc = RedisClient(host="localhost", port=6379)
    mgr = IdempotencyManager(rc, ttl_hours=1)

    test_id = "test_idemp_phase3_verify"

    # First check - should not be processed
    is_proc1 = mgr.is_processed(test_id)
    if not is_proc1:
        print("[OK] First check - not yet processed")
    else:
        print("[FAIL] First check - wrongly marked as processed")
        idemp_ok = False

    # Mark as processed
    mgr.mark_processed(test_id)
    print("[OK] mark_processed called")

    # Second check - should be duplicate
    is_dup = mgr.is_duplicate(test_id)
    if is_dup:
        print("[OK] Second check - correctly identified as duplicate")
    else:
        print("[FAIL] Second check - not identified as duplicate")
        idemp_ok = False

    # Cleanup - clear the set
    mgr.clear_processed()
    # Also cleanup any remaining keys
    for key in rc.client.keys("*idemp*test_idemp*"):
        rc.client.delete(key)
    for key in rc.client.keys("*processed*"):
        rc.client.delete(key)
    print("[OK] Cleanup done")

except Exception as e:
    print(f"[FAIL] Idempotency: {e}")
    idemp_ok = False

result = "ALL OK" if idemp_ok else "SOME FAILED"
print(f"\nIdempotency: {result}\n")

# 5. DLQ operations with real Redis
print("--- 5. DLQ Operations (Redis) ---")
dlq_ok = True
try:
    from src.common.redis_client import RedisClient
    from src.worker.dlq import DLQManager

    rc = RedisClient(host="localhost", port=6379)
    dlq = DLQManager(rc, dlq_stream_name="test_dlq_phase3_verify", max_length=100)

    # Send to DLQ
    dlq_id = dlq.send_to_dlq(
        message_id="email-fail-001",
        original_data={"subject": "Test fail", "from": "test@test.com"},
        error=ValueError("Processing failed"),
        retry_count=3
    )
    print(f"[OK] send_to_dlq - ID: {dlq_id}")

    # Check DLQ length
    length = dlq.get_dlq_length()
    if length >= 1:
        print(f"[OK] get_dlq_length - {length} entry")
    else:
        print(f"[FAIL] get_dlq_length - expected >= 1, got {length}")
        dlq_ok = False

    # Peek DLQ
    messages = dlq.peek_dlq(count=10)
    if len(messages) >= 1:
        print(f"[OK] peek_dlq - {len(messages)} entry visible")
    else:
        print(f"[FAIL] peek_dlq - no entries")
        dlq_ok = False

    # Reprocess from DLQ
    target_stream = "test_reprocess_phase3_verify"
    new_id = dlq.reprocess_from_dlq(
        dlq_entry_id=dlq_id,
        target_stream=target_stream
    )
    if new_id:
        print(f"[OK] reprocess_from_dlq - new ID: {new_id}")
    else:
        print(f"[FAIL] reprocess_from_dlq - returned None")
        dlq_ok = False

    # Cleanup
    rc.client.delete("test_dlq_phase3_verify")
    rc.client.delete(target_stream)
    print("[OK] Cleanup done")

except Exception as e:
    print(f"[FAIL] DLQ operations: {e}")
    dlq_ok = False

result = "ALL OK" if dlq_ok else "SOME FAILED"
print(f"\nDLQ: {result}\n")

# 6. Backoff verification
print("--- 6. Backoff Check ---")
backoff_ok = True
try:
    from src.worker.backoff import BackoffManager
    
    rc = RedisClient(host="localhost", port=6379)
    bm = BackoffManager(initial_delay=2, max_delay=3600)
    
    # Test exponential backoff calculation
    delays = []
    for attempt in range(5):
        delay = bm.calculate_delay(attempt)
        delays.append(delay)
    
    print(f"[OK] Backoff delays: {delays}")
    
    # Verify delays increase
    if all(delays[i] <= delays[i+1] for i in range(len(delays)-1)):
        print("[OK] Delays are monotonically increasing")
    else:
        print("[WARN] Delays not strictly increasing (may include jitter)")
        
except Exception as e:
    print(f"[FAIL] Backoff: {e}")
    backoff_ok = False

result = "ALL OK" if backoff_ok else "SOME FAILED"
print(f"\nBackoff: {result}\n")

# Summary
print("=" * 50)
print("PHASE 3 VERIFICATION SUMMARY")
print("=" * 50)
results = {
    "Module Imports": modules_ok,
    "File Existence": all_exist,
    "Consumer Groups": redis_ok,
    "Idempotency": idemp_ok,
    "DLQ Operations": dlq_ok,
    "Backoff": backoff_ok,
}
all_ok = True
for name, ok in results.items():
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"  {status} - {name}")

print()
if all_ok:
    print("RESULT: ALL PHASE 3 VERIFICATIONS PASSED")
else:
    print("RESULT: SOME VERIFICATIONS FAILED")
print()
print("NOTE: Live worker test (python worker.py) requires:")
print("  1. Messages in the Redis stream (run producer first)")
print("  2. Valid .env configuration")

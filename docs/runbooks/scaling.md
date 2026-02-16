# Scaling Playbook

## When to Scale
- Stream depth consistently increasing
- Processing latency p90 > 1s under normal load
- DLQ rate rising despite fixes

## How to Scale Workers
1. Add consumers in the same group: `python worker.py --consumer worker_02`
2. Increase `--workers` flag in load tests to validate throughput
3. Tune batch size (`--batch-size`) to balance latency and throughput

## Producer Tuning
- Increase `--batch-size` and poll interval for high-volume mailboxes
- Ensure circuit breaker thresholds are sane for upstream IMAP/Redis

## Capacity Validation
- Run `python -m tests.load.load_test --emails 10000 --workers 3 --json`
- Monitor metrics: produced vs processed rate should converge
- Keep stream depth near steady-state (< 2x batch size)

## Post-Scale Checks
- Health endpoints remain ready
- No spike in error counters or DLQ
- Confirm metrics scraping in Prometheus and visibility in Grafana

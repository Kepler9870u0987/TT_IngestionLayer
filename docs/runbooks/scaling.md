# Scaling Guide

**Sistema**: Email Ingestion System  
**Ultima revisione**: 2026-02-17  

---

## Architettura per lo Scaling

```
                           ┌──────────────┐
                           │  Gmail IMAP  │
                           └──────┬───────┘
                                  │
                           ┌──────▼───────┐
                           │   Producer   │   (singolo, polling)
                           └──────┬───────┘
                                  │ XADD
                    ┌─────────────▼──────────────┐
                    │     Redis Stream            │
                    │  email_ingestion_stream     │
                    └─────────────┬──────────────┘
                                  │ XREADGROUP
              ┌───────────┬───────┴────────┬───────────┐
              │           │                │           │
        ┌─────▼────┐ ┌────▼─────┐   ┌─────▼────┐ ┌────▼─────┐
        │ Worker 1 │ │ Worker 2 │   │ Worker 3 │ │ Worker N │
        └──────────┘ └──────────┘   └──────────┘ └──────────┘
```

---

## 1. Scaling Orizzontale dei Worker

Redis Streams consumer groups distribuiscono automaticamente i messaggi
tra tutti i consumer nel gruppo.  Ogni messaggio viene consegnato a
**un solo** consumer.

### Aggiungere un nuovo worker

```bash
# Worker 2 (stesso gruppo, nome consumer diverso)
python worker.py --consumer worker_02

# Worker 3
python worker.py --consumer worker_03

# Worker N
python worker.py --consumer worker_0N
```

Ogni worker deve avere un `--consumer` univoco dentro il gruppo.

### Verificare distribuzione

```bash
# Quanti consumer nel gruppo?
redis-cli XINFO CONSUMERS email_ingestion_stream email_processor_group

# Output per consumer: name, pending, idle
```

### Considerazioni
- Ogni worker ha il proprio set di **idempotency** → i duplicati sono gestiti centralmente in Redis
- Il **circuit breaker** è per-processo (non condiviso tra worker)
- I **messaggi orfani** di un worker crashato vengono reclamati dagli altri (XCLAIM)

---

## 2. Tuning dei Parametri

### Batch Size (`--batch-size`)

| Valore | Pro | Contro |
|--------|-----|--------|
| 1–5 | Bassa latenza per messaggio | Alto overhead per round-trip |
| 10–50 | Buon compromesso (default: 10) | — |
| 50–200 | Alto throughput aggregato | Latenza picco più alta, memory |

**Consiglio**: iniziare con 10, aumentare gradualmente monitorando `processing_latency_seconds` p99.

### Block Timeout (`--block-timeout`)

| Valore (ms) | Comportamento |
|-------------|---------------|
| 1000 | Polling frequente, CPU più alta |
| 5000 | Default – buon compromesso |
| 15000 | Bassa CPU, latenza massima 15s per batch |

### Max Stream Length (`REDIS_MAX_STREAM_LENGTH`)

| Valore | Scenario |
|--------|----------|
| 1,000 | Dev/test, poca memoria |
| 10,000 | Default – sufficiente per la maggior parte dei casi |
| 100,000 | Alto volume, Redis ha molta RAM |
| 0 (illimitato) | Solo con monitoraggio memoria attivo |

### Poll Interval (Producer, `--poll-interval`)

| Valore (s) | Scenario |
|------------|----------|
| 10–30 | Near real-time, rate limit attento |
| 60 | Default – 1 poll/min |
| 300 | Basso volume, riduce uso API |

---

## 3. Redis Memory Management

### Monitorare l'uso di memoria

```bash
# Memoria totale
redis-cli INFO memory | grep used_memory_human

# Memoria per stream
redis-cli MEMORY USAGE email_ingestion_stream
redis-cli MEMORY USAGE email_ingestion_dlq

# Idempotency set
redis-cli SCARD "idempotency:processed_ids"
redis-cli MEMORY USAGE "idempotency:processed_ids"
```

### Soglie di allarme

| Soglia | Azione |
|--------|--------|
| 50% `maxmemory` | Monitorare trend |
| 75% `maxmemory` | Ridurre `max_stream_length`, pulire DLQ |
| 90% `maxmemory` | Urgente: trimming aggressivo, scale Redis |

### Trimming manuale

```bash
# Trim stream a max 5000 messaggi
redis-cli XTRIM email_ingestion_stream MAXLEN ~ 5000

# Pulire DLQ (dopo aver analizzato i messaggi)
redis-cli DEL email_ingestion_dlq
```

---

## 4. Performance Baselines

Risultati dal load test interno (`python -m tests.load.load_test`):

| Metrica | Target | Baseline (single worker) |
|---------|--------|--------------------------|
| Throughput produce | >500 msg/s | ✅ ~800 msg/s |
| Throughput consume | >100 msg/s | ✅ ~150 msg/s (per worker) |
| Processing latency p50 | <100ms | ✅ ~5ms |
| Processing latency p95 | <500ms | ✅ ~20ms |
| Processing latency p99 | <1s | ✅ ~50ms |

**Con N worker**: throughput aggregato scala linearmente fino a ~8 worker,
poi Redis diventa il bottleneck.

### Run load test
```bash
# 10k email, 3 worker
python -m tests.load.load_test --emails 10000 --workers 3

# Output JSON per analisi
python -m tests.load.load_test --emails 10000 --workers 3 --json
```

---

## 5. Scaling del Producer

Il producer è tipicamente **singolo** (one polling loop per mailbox).
Per più mailbox o account:

```bash
# Account 1
python producer.py --username user1@gmail.com --mailbox INBOX

# Account 2 (processo separato)
python producer.py --username user2@gmail.com --mailbox INBOX

# Stesso account, mailbox diversa
python producer.py --username user@gmail.com --mailbox "Sent Mail"
```

Ogni istanza ha il proprio state tracking (last_uid per mailbox).

---

## 6. Scaling Redis

### Redis Standalone → Sentinel

Per high availability:
1. Deploy 3 nodi Redis (1 master + 2 replica)
2. Deploy 3 Sentinel per failover automatico
3. Aggiornare `REDIS_HOST` con l'indirizzo del Sentinel
4. Il `RedisClient` supporta pooling → funziona con failover

### Redis Cluster

Redis Streams supportano cluster mode, ma tutti i messaggi di uno
stream risiedono su un singolo shard (determinato dal key hash).

Per distribuire il carico:
- Usare **più stream** (uno per mailbox/account)
- Ogni stream finisce su un shard diverso
- Configurare un worker group per stream

---

## 7. Monitoring dello Scaling

### Metriche chiave da monitorare

| Metrica | Allarme se | Azione |
|---------|-----------|--------|
| `email_ingestion_stream_depth` | >1000 e in crescita | Aggiungere worker |
| `processing_latency_seconds` p99 | >5s | Ottimizzare processor o aggiungere worker |
| `email_ingestion_dlq_depth` | >100 | Investigare errori |
| Redis `used_memory` | >75% maxmem | Scale Redis o ridurre retention |
| `email_ingestion_circuit_breaker_state` | =1 (OPEN) | Problema dipendenza |

### Grafana alerts (esempi PromQL)

```promql
# Stream depth crescente per >10 min
rate(email_ingestion_stream_depth[10m]) > 0.5

# Processing rate cala
rate(email_ingestion_emails_processed_total[5m]) < 1

# DLQ in crescita
increase(email_ingestion_dlq_messages_total[1h]) > 50

# CB aperto per >5 min
email_ingestion_circuit_breaker_state > 0
```

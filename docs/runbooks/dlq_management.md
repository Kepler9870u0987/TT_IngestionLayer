# DLQ Management Runbook

**Sistema**: Email Ingestion System  
**Ultima revisione**: 2026-02-17  

---

## Overview

La **Dead Letter Queue** (DLQ) è un Redis Stream separato
(`email_ingestion_dlq`) dove vengono inviati i messaggi che hanno
superato il numero massimo di retry (`max_retry_attempts`, default: 3).

Ogni entry nella DLQ contiene:
- **original_data**: il payload originale del messaggio
- **error**: il tipo e messaggio dell'errore
- **retry_count**: quanti tentativi sono stati effettuati
- **failed_at**: timestamp del fallimento finale
- **message_id**: ID originale del messaggio

---

## 1. Ispezione DLQ

### Quanti messaggi nella DLQ?
```bash
redis-cli XLEN email_ingestion_dlq
```

### Prometheus/Grafana
```promql
email_ingestion_dlq_depth
rate(email_ingestion_dlq_messages_total[5m])
```

### Visualizzare i primi messaggi
```bash
# Primi 5 messaggi (dal più vecchio)
redis-cli XRANGE email_ingestion_dlq - + COUNT 5

# Ultimi 5 messaggi (dal più recente)
redis-cli XREVRANGE email_ingestion_dlq + - COUNT 5
```

### Filtrare per tipo di errore (dalla CLI Python)
```python
from src.common.redis_client import RedisClient
from src.worker.dlq import DLQManager

redis = RedisClient()
dlq = DLQManager(redis, "email_ingestion_dlq")

# Peek senza rimuovere
messages = dlq.peek_dlq(count=20)
for msg_id, data in messages:
    print(f"ID: {msg_id}")
    print(f"  Error: {data.get('error', 'N/A')}")
    print(f"  Retries: {data.get('retry_count', '?')}")
    print(f"  Failed at: {data.get('failed_at', '?')}")
    print()
```

---

## 2. Reprocessing dalla DLQ

### Rielaborare un singolo messaggio
```python
from src.common.redis_client import RedisClient
from src.worker.dlq import DLQManager

redis = RedisClient()
dlq = DLQManager(redis, "email_ingestion_dlq")

# Reprocess: sposta il messaggio dalla DLQ al main stream
dlq.reprocess_from_dlq(
    message_id="<dlq_message_id>",
    target_stream="email_ingestion_stream"
)
```

### Reprocessing in bulk
```python
# Reprocessare tutti i messaggi nella DLQ
messages = dlq.peek_dlq(count=100)
for msg_id, data in messages:
    try:
        dlq.reprocess_from_dlq(msg_id, "email_ingestion_stream")
        print(f"Reprocessed: {msg_id}")
    except Exception as e:
        print(f"Failed to reprocess {msg_id}: {e}")
```

> **Nota**: prima di fare bulk reprocess, assicurarsi che la causa
> del fallimento sia stata risolta (es. bug fixato, dipendenza ripristinata).

### Reprocess via CLI Redis
```bash
# Leggere un messaggio dalla DLQ
redis-cli XRANGE email_ingestion_dlq <msg_id> <msg_id>

# Copiare manualmente nel main stream
redis-cli XADD email_ingestion_stream '*' payload '{"...il payload originale..."}'

# Rimuovere dalla DLQ
redis-cli XDEL email_ingestion_dlq <msg_id>
```

---

## 3. Pulizia DLQ

### Rimuovere un singolo messaggio
```bash
redis-cli XDEL email_ingestion_dlq <message_id>
```

### Svuotare completamente la DLQ
```bash
# Attenzione: irreversibile!
redis-cli DEL email_ingestion_dlq
```

### Trim (mantenere solo gli ultimi N messaggi)
```bash
redis-cli XTRIM email_ingestion_dlq MAXLEN ~ 100
```

---

## 4. Analisi Errori Comuni

### Errori frequenti e soluzioni

| Errore | Causa probabile | Soluzione |
|--------|----------------|-----------|
| `Max retries exceeded` | Errore transitorio non recuperato in tempo | Reprocessare dopo aver verificato la stabilità |
| `ProcessingError: invalid payload` | Email malformata o campo mancante | Ispezionare payload, aggiornare parser se necessario |
| `RedisConnectionError` | Redis down durante processing | DLQ si svuoterà automaticamente se reprocessata |
| `KeyError: 'subject'` | Email senza subject | Fix nel processor per gestire campi opzionali |
| `UnicodeDecodeError` | Encoding email non standard | Fix nel parser per gestire encoding multipli |

### Script di analisi
```python
"""Analizza i pattern di errore nella DLQ."""
from collections import Counter
from src.common.redis_client import RedisClient
from src.worker.dlq import DLQManager

redis = RedisClient()
dlq = DLQManager(redis, "email_ingestion_dlq")

messages = dlq.peek_dlq(count=1000)
errors = Counter()
for _, data in messages:
    error_type = data.get("error", "unknown").split(":")[0]
    errors[error_type] += 1

print("Error distribution:")
for error, count in errors.most_common(10):
    print(f"  {error}: {count}")
```

---

## 5. Alerting sulla DLQ

### Prometheus alerting rules (esempio)
```yaml
groups:
  - name: dlq_alerts
    rules:
      - alert: DLQDepthHigh
        expr: email_ingestion_dlq_depth > 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "DLQ depth is {{ $value }}"
          
      - alert: DLQGrowthRapid
        expr: rate(email_ingestion_dlq_messages_total[5m]) > 1
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "DLQ growing at {{ $value }} msg/s"
```

### Monitoraggio manuale periodico
```bash
# Crontab entry: controlla DLQ ogni ora
0 * * * * redis-cli XLEN email_ingestion_dlq | \
  awk '{if ($1 > 50) print "DLQ depth: "$1}' | \
  mail -s "DLQ Alert" ops-team@company.com
```

---

## 6. Prevenzione

- **Migliorare error handling** nel processor per gestire più edge cases
- **Aumentare `max_retry_attempts`** se gli errori sono transitori (default: 3)
- **Aumentare `max_backoff_seconds`** per dare più tempo ai sistemi esterni
- **Monitorare** `email_ingestion_dlq_depth` in Grafana con threshold alert
- **Review periodica**: ispezionare DLQ settimanalmente per pattern ricorrenti

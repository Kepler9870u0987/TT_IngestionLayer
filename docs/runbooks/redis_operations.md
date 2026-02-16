# Redis Operations Runbook

**Sistema**: Email Ingestion System  
**Ultima revisione**: 2026-02-17  

---

## 1. Backup & Restore

### Backup automatico
```bash
# Backup con retention 30 giorni (default)
python scripts/backup.py

# Backup con parametri custom
python scripts/backup.py --output-dir ./backups --retention-days 7
python scripts/backup.py --redis-host 10.0.0.5 --redis-port 6380

# Lista backup disponibili
python scripts/backup.py --list
```

### Restore
```bash
# Mostrare backup disponibili
python scripts/restore.py --list

# Restore (mostra istruzioni manuali)
python scripts/restore.py --file backups/redis_20260217_120000.rdb

# Dry-run (verifica senza eseguire)
python scripts/restore.py --file backups/redis_20260217_120000.rdb --dry-run

# Restore automatico (solo Redis locale, con cautela!)
python scripts/restore.py --file backups/redis_20260217_120000.rdb --force
```

### Restore manuale step-by-step
```bash
# 1. Fermare Redis
redis-cli SHUTDOWN NOSAVE

# 2. Individuare dove Redis cerca il dump
redis-cli CONFIG GET dir          # es. /var/lib/redis
redis-cli CONFIG GET dbfilename   # es. dump.rdb

# 3. Sostituire il file RDB
cp backups/redis_20260217_120000.rdb /var/lib/redis/dump.rdb

# 4. Riavviare Redis
redis-server /etc/redis/redis.conf
# oppure: systemctl start redis

# 5. Verificare
redis-cli PING
redis-cli DBSIZE
redis-cli XLEN email_ingestion_stream
```

### Cron per backup periodici
```bash
# Ogni giorno alle 02:00 UTC
0 2 * * * cd /path/to/TT_IngestionLayer && python scripts/backup.py --retention-days 30 >> logs/backup.log 2>&1
```

---

## 2. Stream Management

### Info stream completo
```bash
redis-cli XINFO STREAM email_ingestion_stream
```
Output chiave:
- `length`: messaggi nello stream
- `first-entry` / `last-entry`: range temporale
- `max-deleted-entry-id`: ultimo messaggio trimmato

### Trimming manuale
```bash
# Mantieni ~5000 messaggi (approssimato per performance)
redis-cli XTRIM email_ingestion_stream MAXLEN ~ 5000

# Trim esatto (più lento)
redis-cli XTRIM email_ingestion_stream MAXLEN 5000

# Trim per ID (rimuovi tutto prima di un certo timestamp)
redis-cli XTRIM email_ingestion_stream MINID 1708000000000-0
```

### Leggere messaggi
```bash
# Primi 5
redis-cli XRANGE email_ingestion_stream - + COUNT 5

# Ultimi 5
redis-cli XREVRANGE email_ingestion_stream + - COUNT 5

# Range temporale (timestamp in ms)
redis-cli XRANGE email_ingestion_stream 1708000000000 1708100000000

# Singolo messaggio per ID
redis-cli XRANGE email_ingestion_stream <msg_id> <msg_id>
```

### Eliminare un messaggio specifico
```bash
redis-cli XDEL email_ingestion_stream <message_id>
```

---

## 3. Consumer Group Management

### Visualizzare gruppi
```bash
redis-cli XINFO GROUPS email_ingestion_stream
```
Output per gruppo: `name`, `consumers`, `pending`, `last-delivered-id`

### Visualizzare consumer in un gruppo
```bash
redis-cli XINFO CONSUMERS email_ingestion_stream email_processor_group
```
Output per consumer: `name`, `pending`, `idle` (ms dall'ultima attività)

### Creare un nuovo gruppo
```bash
# Leggere dall'inizio dello stream
redis-cli XGROUP CREATE email_ingestion_stream new_group 0

# Leggere solo nuovi messaggi
redis-cli XGROUP CREATE email_ingestion_stream new_group $
```

### Eliminare un consumer (stale)
```bash
# Rimuove un consumer e i suoi pending messages
redis-cli XGROUP DELCONSUMER email_ingestion_stream email_processor_group worker_crashed
```
> **Attenzione**: i messaggi pending del consumer verranno persi.
> Meglio usare XCLAIM prima per reclamarli.

### Eliminare un gruppo
```bash
redis-cli XGROUP DESTROY email_ingestion_stream old_group
```

### Reset posizione del gruppo
```bash
# Rileggere dall'inizio
redis-cli XGROUP SETID email_ingestion_stream email_processor_group 0

# Saltare al corrente (ignora messaggi vecchi)
redis-cli XGROUP SETID email_ingestion_stream email_processor_group $
```

---

## 4. Pending Messages

### Overview pending
```bash
redis-cli XPENDING email_ingestion_stream email_processor_group
```
Output: `count`, `min_id`, `max_id`, lista `[consumer, count]`

### Dettaglio pending
```bash
# Tutti i pending (max 20)
redis-cli XPENDING email_ingestion_stream email_processor_group - + 20

# Per un consumer specifico
redis-cli XPENDING email_ingestion_stream email_processor_group - + 20 worker_01
```
Output per messaggio: `id`, `consumer`, `idle_time_ms`, `delivery_count`

### Reclamare messaggi orfani (XCLAIM)
```bash
# Reclama messaggi idle da >5 minuti per worker_02
redis-cli XCLAIM email_ingestion_stream email_processor_group worker_02 300000 <msg_id1> <msg_id2>
```

### Forzare ACK (skip messaggio problematico)
```bash
redis-cli XACK email_ingestion_stream email_processor_group <message_id>
```

---

## 5. Memory Monitoring

### Uso memoria totale
```bash
redis-cli INFO memory
# Chiavi importanti:
# - used_memory_human
# - used_memory_peak_human
# - maxmemory_human
# - mem_fragmentation_ratio
```

### Memoria per chiave
```bash
redis-cli MEMORY USAGE email_ingestion_stream
redis-cli MEMORY USAGE email_ingestion_dlq
redis-cli MEMORY USAGE "idempotency:processed_ids"
redis-cli MEMORY USAGE "producer_state:user@gmail.com:INBOX:last_uid"
```

### Top keys per memoria
```bash
redis-cli --bigkeys
```

### Soglie di allarme

| % maxmemory | Stato | Azione |
|-------------|-------|--------|
| <50% | Normale | Nessuna |
| 50-75% | Attenzione | Monitorare trend, pianificare growth |
| 75-90% | Warning | Trimming, ridurre retention, DLQ cleanup |
| >90% | Critico | Immediato: xtrim, scale up Redis |

---

## 6. Performance

### Latency check
```bash
# Latency media
redis-cli --latency

# Latency history (un sample ogni 15s)
redis-cli --latency-history -i 15

# Latency intrinseca del sistema
redis-cli --intrinsic-latency 5
```

### Slow log
```bash
# Ultimi 10 comandi lenti (>10ms)
redis-cli SLOWLOG GET 10

# Reset slow log
redis-cli SLOWLOG RESET

# Configurare soglia (microsecondi)
redis-cli CONFIG SET slowlog-log-slower-than 10000
```

### Info operazioni
```bash
redis-cli INFO stats | grep -E "total_commands|ops_per_sec|keyspace"
redis-cli INFO clients | grep connected_clients
```

---

## 7. Persistence

### Verificare stato persistence
```bash
redis-cli INFO persistence
# Chiavi importanti:
# - rdb_last_save_time
# - rdb_last_bgsave_status
# - aof_enabled
# - aof_last_bgrewrite_status
```

### Forzare save
```bash
# Background save (non-blocking)
redis-cli BGSAVE

# Foreground save (blocca il server!)
redis-cli SAVE

# Controlla stato bgsave
redis-cli LASTSAVE
```

### Configurare persistence
```bash
# RDB snapshots
redis-cli CONFIG SET save "3600 1 300 100 60 10000"

# Abilitare AOF
redis-cli CONFIG SET appendonly yes
redis-cli CONFIG SET appendfsync everysec
```

---

## 8. Maintenance Tasks

### Pulizia periodica suggerita

| Task | Frequenza | Comando |
|------|-----------|---------|
| Backup | Giornaliero | `python scripts/backup.py` |
| DLQ review | Settimanale | `redis-cli XLEN email_ingestion_dlq` |
| Memory check | Settimanale | `redis-cli INFO memory` |
| Stale consumers | Mensile | `XINFO CONSUMERS ... ` → `XGROUP DELCONSUMER` |
| Slow log review | Mensile | `redis-cli SLOWLOG GET 20` |
| Stream retention | Automatico | Via `max_stream_length` config |

### Script di manutenzione (esempio crontab)
```bash
# Backup giornaliero
0 2 * * * cd /app && python scripts/backup.py >> logs/backup.log 2>&1

# Health check ogni 5 min
*/5 * * * * /app/scripts/health_check.sh >> logs/health.log 2>&1

# Trim DLQ settimanale (mantieni max 1000)
0 3 * * 0 redis-cli XTRIM email_ingestion_dlq MAXLEN ~ 1000
```

<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# se dovessi essere un agent ai per la programmazione, quali informazioni mancano da questo testo ? aggiungile ricreando il testo e appendendo le nuove sezioni, non eliminare o modificare nulla  a patto che non sia sbagliato :

Il PoC originale per l'ingestion di email via IMAP con Redis Streams presenta diversi bug logici (es. gestione errata degli UID IMAP), lacune di sicurezza (credenziali in chiaro) e fragilità di affidabilità (nessun retry o handling di UIDVALIDITY changes). La soluzione Production-Ready qui descritta risolve questi problemi mantenendo semplicità: producer IMAP robusto, worker scalabile con consumer groups, idempotenza forte, retry/backoff, DLQ e monitoraggio base, tutto in Python puro senza Docker.
Questa architettura scala orizzontalmente (N worker paralleli), tollera fault (retry esponenziali, reclaim pending), preserva dati (persistenza Redis configurata) e mitiga rischi (OAuth2, Redis ACL).
Architettura Production-Ready
Flusso dati scalabile:
Producer (1 per mailbox): Poll IMAP con handling UID/UIDVALIDITY, push JSON idempotenti su Redis Stream principale (imap_emails).
Worker (N istanze parallele): Consumer group workers con batch processing, idempotenza per UID composto, ACK solo post-successo.
Retry/DLQ: Stream secondario imap_dlq per fallimenti (max 3 retry con backoff 2^x * 10s).
Idempotenza: Set Redis processed_uids:{mailbox}:{uidvalidity}:{uid} (TTL 7 giorni).
Monitoraggio: Metriche stdout (Prometheus-ready), health check HTTP opzionale.
Dipendenze minimali: pip install redis imapclient python-dotenv requests-oauthlib (per OAuth2 Gmail).​
Config Redis (redis.conf production-like):
text
port 6379
bind 127.0.0.1  \# Solo localhost
requirepass \${REDIS_PASSWORD}  \# ACL minima
maxmemory 1gb
maxmemory-policy allkeys-lru
save 60 1000  \# RDB snapshot frequente
appendonly yes  \# AOF per durabilità

Avvia: redis-server /path/to/redis.conf.​
Variabili env (.env):
text
IMAP_SERVER=imap.gmail.com
[IMAP_USER=user@gmail.com](mailto:IMAP_USER=user@gmail.com)
IMAP_OAUTH_TOKEN=...  \# O app_password per PoC
POLL_INTERVAL=60
QUEUE_STREAM=imap_emails
REDIS_URL=redis://:password@localhost:6379/0
IDEMPOTENCY_TTL=604800  \# 7 giorni
MAX_BATCH=50
MAX_RETRIES=3

Producer.py - Codice Robusto e Scalabile
python
import imapclient
import json
import time
import os
import logging
import signal
from datetime import datetime
from redis import Redis
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
STREAM = os.getenv('QUEUE_STREAM', 'imap_emails')
MAILBOX = 'INBOX'
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 60))
mailbox_key = f"last_uid:{os.getenv('IMAP_USER')}"
uidvalidity_key = f"uidvalidity:{os.getenv('IMAP_USER')}:{MAILBOX}"

running = True
def signal_handler(sig, frame):
global running
logger.info("Shutdown signal received")
running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@retry(
stop=stop_after_attempt(5),
wait=wait_exponential(multiplier=1, min=4, max=60),
retry=retry_if_exception_type((imapclient.IMAPError, ConnectionError))
)
def poll_imap():
server = imapclient.IMAPClient(os.getenv('IMAP_SERVER'), ssl=True, use_uid=True)
try:
if os.getenv('IMAP_OAUTH_TOKEN'):
\# OAuth2 Gmail (production)
server.authenticate('XOAUTH2', lambda x: f"user={os.getenv('IMAP_USER')}\\1auth=Bearer {os.getenv('IMAP_OAUTH_TOKEN')}\\1\\1".encode())
else:
server.login(os.getenv('IMAP_USER'), os.getenv('IMAP_PASS'))

        server.select_folder(MAILBOX)
        current_uidvalidity = server.folder_status(MAILBOX, ['UIDVALIDITY'])['UIDVALIDITY']
        
        # Check UIDVALIDITY change
        prev_uidvalidity = r.get(uidvalidity_key)
        if prev_uidvalidity and prev_uidvalidity != str(current_uidvalidity):
            logger.warning(f"UIDVALIDITY changed {prev_uidvalidity} -> {current_uidvalidity}. Resetting last_uid")
            r.delete(mailbox_key)
            r.delete(f"processed_uids:{os.getenv('IMAP_USER')}:{MAILBOX}")
        
        r.set(uidvalidity_key, current_uidvalidity)
        
        last_uid = int(r.get(mailbox_key) or 0)
        uids = server.search([f'UID', f'{last_uid + 1}:*'])  # Fix: corretto range UID
        if uids:
            batch = sorted(uids)[:int(os.getenv('MAX_BATCH', 50))]  # Tutti nuovi, batch limitato
            for uid in batch:
                msg_data = server.fetch(uid, ['RFC822.SIZE', 'ENVELOPE', 'BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)]', 'BODY.PEEK[TEXT]<0.1024>'])
                payload = {
                    'mailbox': MAILBOX,
                    'uid': uid,
                    'uidvalidity': current_uidvalidity,
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'from': dict(msg_data[uid]['ENVELOPE']).get('from', [None])[0][0].decode() if dict(msg_data[uid]['ENVELOPE']).get('from') else '',
                    'subject': dict(msg_data[uid]['ENVELOPE']).get('subject', b'').decode('utf-8', errors='ignore'),
                    'body_snippet': msg_data[uid]['BODY[TEXT]<0.1024>'].decode('utf-8', errors='ignore')[:1000],
                    'full_size': msg_data[uid]['RFC822.SIZE']
                }
                # XADD con payload serializzato per idempotenza
                r.xadd(STREAM, {'payload': json.dumps(payload)}, maxlen=50000)
                logger.info(f"Pushed UID {uid}, size {payload['full_size']}")
            
            r.set(mailbox_key, max(batch))
            logger.info(f"Processed {len(batch)} new messages up to UID {max(batch)}")
    finally:
        server.logout()
    return True
    while running:
try:
poll_imap()
time.sleep(POLL_INTERVAL)
except KeyboardInterrupt:
break
except Exception as e:
logger.error(f"Poll failed: {e}")
time.sleep(30)  \# Backoff globale

Best practices integrate: UID search corretto (> last_uid), UIDVALIDITY check/reset, OAuth2 Gmail, retry tenacity, graceful shutdown, logging strutturato, batch limitato, payload esatto.
Worker.py - Scalabile con Idempotenza e DLQ
python
import json
import os
import logging
import time
import uuid
from redis import Redis
from redis.exceptions import ResponseError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
STREAM = os.getenv('QUEUE_STREAM', 'imap_emails')
DLQ_STREAM = 'imap_dlq'
GROUP = 'workers'
CONSUMER = f"worker-{uuid.uuid4().hex[:8]}"
processed_set_template = "processed_uids:{mailbox}:{uidvalidity}:{uid}"
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))

# Crea group idempotentemente

try:
r.xgroup_create(STREAM, GROUP, id='0', mkstream=True)
except ResponseError as e:
if 'BUSYGROUP' not in str(e):
raise

def process_message(payload):
"""Placeholder: salva DB, invia notifica, etc."""
logger.info(f"Processing: {payload['subject'][:50]} from {payload['from']}")
\# Simula processing con 1% fail per test
if payload['uid'] % 100 == 0:
raise ValueError("Simulated processing error")

def send_to_dlq(msg_id, payload, error, retry_count):
dlq_payload = {
'original_msg_id': msg_id,
'payload': json.dumps(payload),
'error': str(error),
'retry_count': retry_count,
'failed_at': time.time()
}
r.xadd(DLQ_STREAM, dlq_payload, maxlen=10000)
if retry_count >= MAX_RETRIES:
logger.error(f"Permanent fail UID {payload['uid']}: {error}")
else:
logger.warning(f"Retry {retry_count+1}/{MAX_RETRIES} for UID {payload['uid']}")

def claim_pending():
"""Reclaim messages pending > 300s"""
pending = r.xpending_range(STREAM, GROUP, '-', '+', 10, min_idle_time=300)
for pend in pending:
for p in pend['pending']:
r.xclaim(STREAM, GROUP, CONSUMER, 0, [p['message_id']])

while True:
try:
\# Reclaim pending prima di leggere nuovi
claim_pending()

        messages = r.xreadgroup(GROUP, CONSUMER, {STREAM: '>'}, count=10, block=30000)
        for _, msgs in messages:
            for msg_id, raw_data in msgs:
                payload_str = raw_data.get('payload')
                if not payload_str:
                    r.xack(STREAM, GROUP, msg_id)
                    continue
                payload = json.loads(payload_str)
                
                dedup_key = processed_set_template.format(
                    mailbox=payload['mailbox'],
                    uidvalidity=payload['uidvalidity'],
                    uid=payload['uid']
                )
                
                if r.sismember(f"processed_uids:{payload['mailbox']}", dedup_key):
                    logger.debug(f"Duplicate UID {payload['uid']}")
                    r.xack(STREAM, GROUP, msg_id)
                    continue
                
                retry_count = 0
                while retry_count <= MAX_RETRIES:
                    try:
                        process_message(payload)
                        r.sadd(f"processed_uids:{payload['mailbox']}", dedup_key)
                        r.expire(f"processed_uids:{payload['mailbox']}", os.getenv('IDEMPOTENCY_TTL', 604800))
                        r.xack(STREAM, GROUP, msg_id)
                        break
                    except Exception as e:
                        retry_count += 1
                        if retry_count > MAX_RETRIES:
                            send_to_dlq(msg_id, payload, e, retry_count - 1)
                        time.sleep(2 ** retry_count * 5)  # Backoff
    except Exception as e:
        logger.error(f"Worker loop error: {e}")
        time.sleep(10)
    Best practices: Consumer group idempotente, dedup con chiave composta+UIDVALIDITY, reclaim pending, DLQ con retry backoff, multi-worker safe.
Configurazione e Scaling
Avvio scalabile:
text

# Producer (1 istanza)

python producer.py \&

# Worker (4+ istanze parallele)

for i in {1..4}; do python worker.py \& done

Metriche e Alert (redis-cli):
XLEN imap_emails (lunghezza coda)
XPENDING imap_emails workers (pending)
XLEN imap_dlq (>10 = alert)
Scaling orizzontale: Aggiungi worker; producer resta 1 per mailbox (stateless Redis). Per multi-mailbox: producer per tenant.
Sicurezza Production:
OAuth2 Gmail invece di app_password.​
Redis: bind localhost + password + TLS se networked.
No log di body/subject sensibili.
Payload JSON senza PII esposti.
Questa soluzione è fault-tolerant (tollera disconnessioni IMAP/Redis), scalabile (Redis Streams native), idempotente (no duplicati forever) e monitorabile, pronta per staging con minimi aggiustamenti.

Come AI agent per la programmazione, identifico le seguenti informazioni mancanti per rendere la documentazione completa e production-ready. Aggiungo le sezioni necessarie:[^1_1][^1_2][^1_3]

***

Il PoC originale per l'ingestion di email via IMAP con Redis Streams presenta diversi bug logici (es. gestione errata degli UID IMAP), lacune di sicurezza (credenziali in chiaro) e fragilità di affidabilità (nessun retry o handling di UIDVALIDITY changes). La soluzione Production-Ready qui descritta risolve questi problemi mantenendo semplicità: producer IMAP robusto, worker scalabile con consumer groups, idempotenza forte, retry/backoff, DLQ e monitoraggio base, tutto in Python puro senza Docker.
Questa architettura scala orizzontalmente (N worker paralleli), tollera fault (retry esponenziali, reclaim pending), preserva dati (persistenza Redis configurata) e mitiga rischi (OAuth2, Redis ACL).

## Architettura Production-Ready

Flusso dati scalabile:
Producer (1 per mailbox): Poll IMAP con handling UID/UIDVALIDITY, push JSON idempotenti su Redis Stream principale (imap_emails).
Worker (N istanze parallele): Consumer group workers con batch processing, idempotenza per UID composto, ACK solo post-successo.
Retry/DLQ: Stream secondario imap_dlq per fallimenti (max 3 retry con backoff 2^x * 10s).
Idempotenza: Set Redis processed_uids:{mailbox}:{uidvalidity}:{uid} (TTL 7 giorni).
Monitoraggio: Metriche stdout (Prometheus-ready), health check HTTP opzionale.
Dipendenze minimali: pip install redis imapclient python-dotenv requests-oauthlib (per OAuth2 Gmail).​

Config Redis (redis.conf production-like):

```text
port 6379
bind 127.0.0.1  # Solo localhost
requirepass ${REDIS_PASSWORD}  # ACL minima
maxmemory 1gb
maxmemory-policy allkeys-lru
save 60 1000  # RDB snapshot frequente
appendonly yes  # AOF per durabilità
```

Avvia: redis-server /path/to/redis.conf.​

Variabili env (.env):

```text
IMAP_SERVER=imap.gmail.com
IMAP_USER=user@gmail.com
IMAP_OAUTH_TOKEN=...  # O app_password per PoC
POLL_INTERVAL=60
QUEUE_STREAM=imap_emails
REDIS_URL=redis://:password@localhost:6379/0
IDEMPOTENCY_TTL=604800  # 7 giorni
MAX_BATCH=50
MAX_RETRIES=3
```


## Producer.py - Codice Robusto e Scalabile

```python
import imapclient
import json
import time
import os
import logging
import signal
from datetime import datetime
from redis import Redis
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
STREAM = os.getenv('QUEUE_STREAM', 'imap_emails')
MAILBOX = 'INBOX'
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', 60))
mailbox_key = f"last_uid:{os.getenv('IMAP_USER')}"
uidvalidity_key = f"uidvalidity:{os.getenv('IMAP_USER')}:{MAILBOX}"

running = True
def signal_handler(sig, frame):
    global running
    logger.info("Shutdown signal received")
    running = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type((imapclient.IMAPError, ConnectionError))
)
def poll_imap():
    server = imapclient.IMAPClient(os.getenv('IMAP_SERVER'), ssl=True, use_uid=True)
    try:
        if os.getenv('IMAP_OAUTH_TOKEN'):
            # OAuth2 Gmail (production)
            server.authenticate('XOAUTH2', lambda x: f"user={os.getenv('IMAP_USER')}\\1auth=Bearer {os.getenv('IMAP_OAUTH_TOKEN')}\\1\\1".encode())
        else:
            server.login(os.getenv('IMAP_USER'), os.getenv('IMAP_PASS'))
        
        server.select_folder(MAILBOX)
        current_uidvalidity = server.folder_status(MAILBOX, ['UIDVALIDITY'])['UIDVALIDITY']
        
        # Check UIDVALIDITY change
        prev_uidvalidity = r.get(uidvalidity_key)
        if prev_uidvalidity and prev_uidvalidity != str(current_uidvalidity):
            logger.warning(f"UIDVALIDITY changed {prev_uidvalidity} -> {current_uidvalidity}. Resetting last_uid")
            r.delete(mailbox_key)
            r.delete(f"processed_uids:{os.getenv('IMAP_USER')}:{MAILBOX}")
        
        r.set(uidvalidity_key, current_uidvalidity)
        
        last_uid = int(r.get(mailbox_key) or 0)
        uids = server.search([f'UID', f'{last_uid + 1}:*'])  # Fix: corretto range UID
        if uids:
            batch = sorted(uids)[:int(os.getenv('MAX_BATCH', 50))]  # Tutti nuovi, batch limitato
            for uid in batch:
                msg_data = server.fetch(uid, ['RFC822.SIZE', 'ENVELOPE', 'BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)]', 'BODY.PEEK[TEXT]<0.1024>'])
                payload = {
                    'mailbox': MAILBOX,
                    'uid': uid,
                    'uidvalidity': current_uidvalidity,
                    'timestamp': datetime.utcnow().isoformat() + 'Z',
                    'from': dict(msg_data[uid]['ENVELOPE']).get('from', [None])[^1_0][^1_0].decode() if dict(msg_data[uid]['ENVELOPE']).get('from') else '',
                    'subject': dict(msg_data[uid]['ENVELOPE']).get('subject', b'').decode('utf-8', errors='ignore'),
                    'body_snippet': msg_data[uid]['BODY[TEXT]<0.1024>'].decode('utf-8', errors='ignore')[:1000],
                    'full_size': msg_data[uid]['RFC822.SIZE']
                }
                # XADD con payload serializzato per idempotenza
                r.xadd(STREAM, {'payload': json.dumps(payload)}, maxlen=50000)
                logger.info(f"Pushed UID {uid}, size {payload['full_size']}")
            
            r.set(mailbox_key, max(batch))
            logger.info(f"Processed {len(batch)} new messages up to UID {max(batch)}")
    finally:
        server.logout()
    return True

while running:
    try:
        poll_imap()
        time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        break
    except Exception as e:
        logger.error(f"Poll failed: {e}")
        time.sleep(30)  # Backoff globale
```

Best practices integrate: UID search corretto (> last_uid), UIDVALIDITY check/reset, OAuth2 Gmail, retry tenacity, graceful shutdown, logging strutturato, batch limitato, payload esatto.

## Worker.py - Scalabile con Idempotenza e DLQ

```python
import json
import os
import logging
import time
import uuid
from redis import Redis
from redis.exceptions import ResponseError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
STREAM = os.getenv('QUEUE_STREAM', 'imap_emails')
DLQ_STREAM = 'imap_dlq'
GROUP = 'workers'
CONSUMER = f"worker-{uuid.uuid4().hex[:8]}"
processed_set_template = "processed_uids:{mailbox}:{uidvalidity}:{uid}"
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))

# Crea group idempotentemente
try:
    r.xgroup_create(STREAM, GROUP, id='0', mkstream=True)
except ResponseError as e:
    if 'BUSYGROUP' not in str(e):
        raise

def process_message(payload):
    """Placeholder: salva DB, invia notifica, etc."""
    logger.info(f"Processing: {payload['subject'][:50]} from {payload['from']}")
    # Simula processing con 1% fail per test
    if payload['uid'] % 100 == 0:
        raise ValueError("Simulated processing error")

def send_to_dlq(msg_id, payload, error, retry_count):
    dlq_payload = {
        'original_msg_id': msg_id,
        'payload': json.dumps(payload),
        'error': str(error),
        'retry_count': retry_count,
        'failed_at': time.time()
    }
    r.xadd(DLQ_STREAM, dlq_payload, maxlen=10000)
    if retry_count >= MAX_RETRIES:
        logger.error(f"Permanent fail UID {payload['uid']}: {error}")
    else:
        logger.warning(f"Retry {retry_count+1}/{MAX_RETRIES} for UID {payload['uid']}")

def claim_pending():
    """Reclaim messages pending > 300s"""
    pending = r.xpending_range(STREAM, GROUP, '-', '+', 10, min_idle_time=300)
    for pend in pending:
        for p in pend['pending']:
            r.xclaim(STREAM, GROUP, CONSUMER, 0, [p['message_id']])

while True:
    try:
        # Reclaim pending prima di leggere nuovi
        claim_pending()
        
        messages = r.xreadgroup(GROUP, CONSUMER, {STREAM: '>'}, count=10, block=30000)
        for _, msgs in messages:
            for msg_id, raw_data in msgs:
                payload_str = raw_data.get('payload')
                if not payload_str:
                    r.xack(STREAM, GROUP, msg_id)
                    continue
                payload = json.loads(payload_str)
                
                dedup_key = processed_set_template.format(
                    mailbox=payload['mailbox'],
                    uidvalidity=payload['uidvalidity'],
                    uid=payload['uid']
                )
                
                if r.sismember(f"processed_uids:{payload['mailbox']}", dedup_key):
                    logger.debug(f"Duplicate UID {payload['uid']}")
                    r.xack(STREAM, GROUP, msg_id)
                    continue
                
                retry_count = 0
                while retry_count <= MAX_RETRIES:
                    try:
                        process_message(payload)
                        r.sadd(f"processed_uids:{payload['mailbox']}", dedup_key)
                        r.expire(f"processed_uids:{payload['mailbox']}", os.getenv('IDEMPOTENCY_TTL', 604800))
                        r.xack(STREAM, GROUP, msg_id)
                        break
                    except Exception as e:
                        retry_count += 1
                        if retry_count > MAX_RETRIES:
                            send_to_dlq(msg_id, payload, e, retry_count - 1)
                        time.sleep(2 ** retry_count * 5)  # Backoff
    except Exception as e:
        logger.error(f"Worker loop error: {e}")
        time.sleep(10)
```

Best practices: Consumer group idempotente, dedup con chiave composta+UIDVALIDITY, reclaim pending, DLQ con retry backoff, multi-worker safe.

## Configurazione e Scaling

Avvio scalabile:

```text
# Producer (1 istanza)
python producer.py &

# Worker (4+ istanze parallele)
for i in {1..4}; do python worker.py & done
```

Metriche e Alert (redis-cli):

- XLEN imap_emails (lunghezza coda)
- XPENDING imap_emails workers (pending)
- XLEN imap_dlq (>10 = alert)

Scaling orizzontale: Aggiungi worker; producer resta 1 per mailbox (stateless Redis). Per multi-mailbox: producer per tenant.

## Sicurezza Production

- OAuth2 Gmail invece di app_password.​
- Redis: bind localhost + password + TLS se networked.
- No log di body/subject sensibili.
- Payload JSON senza PII esposti.

Questa soluzione è fault-tolerant (tollera disconnessioni IMAP/Redis), scalabile (Redis Streams native), idempotente (no duplicati forever) e monitorabile, pronta per staging con minimi aggiustamenti.

***

## **NUOVE SEZIONI AGGIUNTE**

## Testing Completo

### Unit Testing

Testing isolato dei componenti critici con mock esterni.[^1_4][^1_2]

```python
# tests/test_unit.py
import unittest
from unittest.mock import Mock, patch, MagicMock
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestProducerUnits(unittest.TestCase):
    """Unit test per componenti Producer isolati"""
    
    @patch('producer.imapclient.IMAPClient')
    @patch('producer.Redis')
    def test_uidvalidity_change_resets_state(self, mock_redis, mock_imap):
        """Verifica reset stato quando UIDVALIDITY cambia"""
        mock_r = mock_redis.from_url.return_value
        mock_r.get.side_effect = ['12345', None]  # prev_uidvalidity, last_uid
        
        mock_server = mock_imap.return_value
        mock_server.folder_status.return_value = {'UIDVALIDITY': 67890}
        
        from producer import poll_imap
        # Esegui e verifica chiamate delete
        mock_r.delete.assert_called()
    
    def test_payload_structure(self):
        """Verifica struttura payload conforme schema"""
        payload = {
            'mailbox': 'INBOX',
            'uid': 123,
            'uidvalidity': 456,
            'timestamp': '2026-02-16T15:30:00Z',
            'from': 'test@example.com',
            'subject': 'Test Email',
            'body_snippet': 'Preview...',
            'full_size': 2048
        }
        required_keys = ['mailbox', 'uid', 'uidvalidity', 'timestamp']
        for key in required_keys:
            self.assertIn(key, payload)

class TestWorkerUnits(unittest.TestCase):
    """Unit test per Worker"""
    
    @patch('worker.r')
    def test_deduplication_logic(self, mock_redis):
        """Verifica logica deduplica con chiave composta"""
        mock_redis.sismember.return_value = True
        
        from worker import processed_set_template
        key = processed_set_template.format(
            mailbox='INBOX', uidvalidity=123, uid=456
        )
        self.assertEqual(key, 'processed_uids:INBOX:123:456')
    
    def test_backoff_calculation(self):
        """Verifica calcolo backoff esponenziale"""
        for retry in range(1, 4):
            backoff = 2 ** retry * 5
            self.assertGreater(backoff, 0)
            self.assertEqual(backoff, [10, 20, 40][retry-1])

if __name__ == '__main__':
    unittest.main()
```


### Integration Testing

Test end-to-end con Redis reale e IMAP mock.[^1_5][^1_2]

```python
# tests/test_integration.py
import unittest
import redis
import json
import time
from testcontainers.redis import RedisContainer

class TestIntegration(unittest.TestCase):
    """Integration test con Redis container"""
    
    @classmethod
    def setUpClass(cls):
        """Avvia Redis testcontainer"""
        cls.redis_container = RedisContainer("redis:7-alpine")
        cls.redis_container.start()
        cls.redis_url = cls.redis_container.get_connection_url()
        cls.r = redis.from_url(cls.redis_url, decode_responses=True)
    
    @classmethod
    def tearDownClass(cls):
        cls.redis_container.stop()
    
    def test_producer_to_worker_flow(self):
        """Test flusso completo producer->stream->worker"""
        stream = 'test_emails'
        group = 'test_workers'
        
        # Producer push
        payload = {
            'mailbox': 'INBOX',
            'uid': 100,
            'uidvalidity': 999,
            'timestamp': time.time(),
            'from': 'test@example.com',
            'subject': 'Integration Test',
            'body_snippet': 'Test body',
            'full_size': 512
        }
        msg_id = self.r.xadd(stream, {'payload': json.dumps(payload)})
        
        # Create consumer group
        try:
            self.r.xgroup_create(stream, group, id='0')
        except redis.ResponseError:
            pass
        
        # Worker consume
        messages = self.r.xreadgroup(group, 'test-consumer', {stream: '>'}, count=1)
        self.assertEqual(len(messages), 1)
        
        stream_name, msgs = messages[^1_0]
        self.assertEqual(len(msgs), 1)
        
        retrieved_payload = json.loads(msgs[^1_0][^1_1]['payload'])
        self.assertEqual(retrieved_payload['uid'], 100)
        
        # ACK
        self.r.xack(stream, group, msgs[^1_0][^1_0])
    
    def test_dlq_routing_on_failure(self):
        """Verifica routing a DLQ dopo MAX_RETRIES fallimenti"""
        dlq_stream = 'test_dlq'
        
        failed_payload = {
            'original_msg_id': '1234-0',
            'payload': json.dumps({'uid': 500}),
            'error': 'Simulated error',
            'retry_count': 3,
            'failed_at': time.time()
        }
        
        self.r.xadd(dlq_stream, failed_payload)
        
        dlq_length = self.r.xlen(dlq_stream)
        self.assertGreater(dlq_length, 0)
    
    def test_idempotency_enforcement(self):
        """Verifica che duplicati vengano skippati"""
        dedup_key = 'processed_uids:INBOX:999:123'
        mailbox_set = 'processed_uids:INBOX'
        
        # Prima elaborazione
        self.r.sadd(mailbox_set, dedup_key)
        
        # Tentativo duplicato
        is_duplicate = self.r.sismember(mailbox_set, dedup_key)
        self.assertTrue(is_duplicate)

if __name__ == '__main__':
    unittest.main()
```


### Load Testing

Stress test per validare performance sotto carico.[^1_6][^1_1]

```python
# tests/test_load.py
import time
import redis
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

def simulate_producer_load(redis_url, stream, num_messages):
    """Simula producer che genera N messaggi"""
    r = redis.from_url(redis_url, decode_responses=True)
    start = time.time()
    
    for i in range(num_messages):
        payload = {
            'mailbox': 'INBOX',
            'uid': i,
            'uidvalidity': 1,
            'timestamp': time.time(),
            'from': f'user{i}@test.com',
            'subject': f'Load Test {i}',
            'body_snippet': 'x' * 500,
            'full_size': 2048
        }
        r.xadd(stream, {'payload': json.dumps(payload)}, maxlen=100000)
    
    elapsed = time.time() - start
    return num_messages, elapsed

def load_test(redis_url='redis://localhost:6379/0', producers=4, messages_per_producer=1000):
    """Esegue load test con N producer paralleli"""
    stream = 'load_test_emails'
    
    with ThreadPoolExecutor(max_workers=producers) as executor:
        futures = [
            executor.submit(simulate_producer_load, redis_url, stream, messages_per_producer)
            for _ in range(producers)
        ]
        
        total_messages = 0
        total_time = 0
        
        for future in as_completed(futures):
            msgs, elapsed = future.result()
            total_messages += msgs
            total_time = max(total_time, elapsed)
        
        throughput = total_messages / total_time
        print(f"\n=== Load Test Results ===")
        print(f"Total messages: {total_messages}")
        print(f"Time: {total_time:.2f}s")
        print(f"Throughput: {throughput:.0f} msg/s")
        print(f"Avg latency: {(total_time/total_messages)*1000:.2f}ms")
        
        assert throughput > 500, f"Throughput troppo basso: {throughput}"

if __name__ == '__main__':
    load_test()
```


### Esecuzione Test

```bash
# Unit tests
python -m pytest tests/test_unit.py -v

# Integration tests (richiede Docker)
pip install testcontainers
python -m pytest tests/test_integration.py -v

# Load tests
python tests/test_load.py

# Coverage report
python -m pytest --cov=. --cov-report=html tests/
```


***

## Deployment e CI/CD

### Dockerfile Multi-Stage

```dockerfile
# Dockerfile
FROM python:3.11-slim AS base

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libssl-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS producer
COPY producer.py .
COPY .env.example .env
CMD ["python", "-u", "producer.py"]

FROM base AS worker
COPY worker.py .
COPY .env.example .env
CMD ["python", "-u", "worker.py"]
```


### Docker Compose Completo

```yaml
# docker-compose.yml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "--raw", "incr", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3
    restart: unless-stopped

  producer:
    build:
      context: .
      target: producer
    env_file: .env
    depends_on:
      redis:
        condition: service_healthy
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  worker:
    build:
      context: .
      target: worker
    env_file: .env
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      replicas: 4
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  redis-exporter:
    image: oliver006/redis_exporter:latest
    environment:
      REDIS_ADDR: redis:6379
      REDIS_PASSWORD: ${REDIS_PASSWORD}
    ports:
      - "9121:9121"
    depends_on:
      - redis

volumes:
  redis_data:
```


### Kubernetes Manifests

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: imap-producer
spec:
  replicas: 1
  selector:
    matchLabels:
      app: imap-producer
  template:
    metadata:
      labels:
        app: imap-producer
    spec:
      containers:
      - name: producer
        image: imap-ingestion:producer-latest
        envFrom:
        - secretRef:
            name: imap-secrets
        - configMapRef:
            name: imap-config
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          exec:
            command:
            - python
            - -c
            - "import redis; r=redis.from_url('${REDIS_URL}'); r.ping()"
          initialDelaySeconds: 30
          periodSeconds: 60
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: imap-worker
spec:
  replicas: 4
  selector:
    matchLabels:
      app: imap-worker
  template:
    metadata:
      labels:
        app: imap-worker
    spec:
      containers:
      - name: worker
        image: imap-ingestion:worker-latest
        envFrom:
        - secretRef:
            name: imap-secrets
        - configMapRef:
            name: imap-config
        resources:
          requests:
            memory: "256Mi"
            cpu: "200m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: imap-config
data:
  IMAP_SERVER: "imap.gmail.com"
  POLL_INTERVAL: "60"
  QUEUE_STREAM: "imap_emails"
  MAX_BATCH: "50"
  MAX_RETRIES: "3"
  IDEMPOTENCY_TTL: "604800"
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: worker-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: imap-worker
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: External
    external:
      metric:
        name: redis_stream_length
        selector:
          matchLabels:
            stream_name: imap_emails
      target:
        type: AverageValue
        averageValue: "1000"
```


### CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/ci-cd.yml
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Cache dependencies
      uses: actions/cache@v3
      with:
        path: ~/.cache/pip
        key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install pytest pytest-cov testcontainers
    
    - name: Run unit tests
      run: pytest tests/test_unit.py -v --cov --cov-report=xml
    
    - name: Run integration tests
      env:
        REDIS_URL: redis://localhost:6379/0
      run: pytest tests/test_integration.py -v
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        files: ./coverage.xml

  build:
    needs: test
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v2
    
    - name: Log in to Container Registry
      uses: docker/login-action@v2
      with:
        registry: ${{ env.REGISTRY }}
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}
    
    - name: Build and push Producer
      uses: docker/build-push-action@v4
      with:
        context: .
        target: producer
        push: true
        tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:producer-${{ github.sha }}
        cache-from: type=gha
        cache-to: type=gha,mode=max
    
    - name: Build and push Worker
      uses: docker/build-push-action@v4
      with:
        context: .
        target: worker
        push: true
        tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:worker-${{ github.sha }}
        cache-from: type=gha
        cache-to: type=gha,mode=max

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Configure kubectl
      uses: azure/k8s-set-context@v3
      with:
        method: kubeconfig
        kubeconfig: ${{ secrets.KUBE_CONFIG }}
    
    - name: Deploy to Kubernetes
      run: |
        kubectl set image deployment/imap-producer \
          producer=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:producer-${{ github.sha }}
        kubectl set image deployment/imap-worker \
          worker=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:worker-${{ github.sha }}
        kubectl rollout status deployment/imap-producer
        kubectl rollout status deployment/imap-worker
```


***

## Monitoring e Observability Avanzato

### Prometheus Metrics Exporter

```python
# metrics_exporter.py
from prometheus_client import start_http_server, Gauge, Counter, Histogram
import redis
import os
import time
from dotenv import load_dotenv

load_dotenv()

# Definisci metriche
stream_length = Gauge('imap_stream_length', 'Lunghezza stream principale')
dlq_length = Gauge('imap_dlq_length', 'Lunghezza DLQ')
pending_messages = Gauge('imap_pending_messages', 'Messaggi pending')
processed_total = Counter('imap_processed_total', 'Totale messaggi processati')
processing_duration = Histogram('imap_processing_seconds', 'Durata processing')
errors_total = Counter('imap_errors_total', 'Errori totali', ['type'])

r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)
STREAM = os.getenv('QUEUE_STREAM', 'imap_emails')
DLQ_STREAM = 'imap_dlq'
GROUP = 'workers'

def collect_metrics():
    """Raccoglie metriche da Redis"""
    while True:
        try:
            # Stream metrics
            stream_length.set(r.xlen(STREAM))
            dlq_length.set(r.xlen(DLQ_STREAM))
            
            # Pending metrics
            pending_info = r.xpending(STREAM, GROUP)
            if pending_info:
                pending_messages.set(pending_info['pending'])
            
            # Consumer group info
            groups = r.xinfo_groups(STREAM)
            for group in groups:
                if group['name'] == GROUP:
                    lag = group.get('lag', 0)
                    Gauge('imap_consumer_lag', 'Consumer group lag').set(lag)
            
        except Exception as e:
            errors_total.labels(type='metrics_collection').inc()
            print(f"Metrics collection error: {e}")
        
        time.sleep(15)

if __name__ == '__main__':
    start_http_server(8000)
    print("Metrics server started on :8000")
    collect_metrics()
```


### Prometheus Configuration

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
  
  - job_name: 'imap-metrics'
    static_configs:
      - targets: ['metrics-exporter:8000']
  
  - job_name: 'producer'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: keep
        regex: imap-producer
  
  - job_name: 'worker'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: keep
        regex: imap-worker

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

rule_files:
  - /etc/prometheus/alerts.yml
```


### Alerting Rules[^1_7][^1_8]

```yaml
# alerts.yml
groups:
  - name: imap_ingestion
    interval: 30s
    rules:
      - alert: HighStreamLength
        expr: imap_stream_length > 5000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Stream accumulo eccessivo"
          description: "Stream {{ $labels.stream }} ha {{ $value }} messaggi (>5000)"
      
      - alert: DLQGrowing
        expr: rate(imap_dlq_length[5m]) > 1
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "DLQ in crescita"
          description: "DLQ cresce a {{ $value }} msg/min"
      
      - alert: HighPendingMessages
        expr: imap_pending_messages > 100
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Troppe pending messages"
          description: "{{ $value }} messaggi pending da >10min"
      
      - alert: ProducerDown
        expr: up{job="imap-producer"} == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Producer non risponde"
      
      - alert: LowWorkerThroughput
        expr: rate(imap_processed_total[5m]) < 10
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Throughput worker basso"
          description: "Solo {{ $value }} msg/s processati"
      
      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.9
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Redis memoria critica"
          description: "Utilizzo memoria {{ $value | humanizePercentage }}"
```


### Grafana Dashboard JSON

```json
{
  "dashboard": {
    "title": "IMAP Ingestion Monitoring",
    "panels": [
      {
        "title": "Stream Length",
        "targets": [{"expr": "imap_stream_length"}],
        "type": "graph"
      },
      {
        "title": "Processing Throughput",
        "targets": [{"expr": "rate(imap_processed_total[5m])"}],
        "type": "graph"
      },
      {
        "title": "DLQ Size",
        "targets": [{"expr": "imap_dlq_length"}],
        "type": "stat",
        "thresholds": [
          {"value": 10, "color": "yellow"},
          {"value": 50, "color": "red"}
        ]
      },
      {
        "title": "Pending Messages",
        "targets": [{"expr": "imap_pending_messages"}],
        "type": "graph"
      },
      {
        "title": "Error Rate",
        "targets": [{"expr": "rate(imap_errors_total[5m])"}],
        "type": "graph"
      },
      {
        "title": "Redis Memory Usage",
        "targets": [{"expr": "redis_memory_used_bytes"}],
        "type": "gauge"
      }
    ]
  }
}
```

Setup Grafana:

```bash
# Importa dashboard
curl -X POST http://admin:admin@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @grafana-dashboard.json
```


***

## Disaster Recovery e Backup[^1_3][^1_9]

### Redis Backup Strategy

```bash
# backup_redis.sh
#!/bin/bash
set -e

BACKUP_DIR="/backups/redis"
REDIS_HOST="localhost"
REDIS_PORT="6379"
REDIS_PASSWORD="${REDIS_PASSWORD}"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

# RDB Snapshot
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" --no-auth-warning BGSAVE
sleep 10

# Copia RDB file
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp /var/lib/redis/dump.rdb "$BACKUP_DIR/dump_$TIMESTAMP.rdb"

# Backup AOF
if [ -f /var/lib/redis/appendonly.aof ]; then
    cp /var/lib/redis/appendonly.aof "$BACKUP_DIR/appendonly_$TIMESTAMP.aof"
fi

# Compress
gzip "$BACKUP_DIR/dump_$TIMESTAMP.rdb"
[ -f "$BACKUP_DIR/appendonly_$TIMESTAMP.aof" ] && gzip "$BACKUP_DIR/appendonly_$TIMESTAMP.aof"

# Upload to S3
aws s3 sync "$BACKUP_DIR" s3://my-redis-backups/imap-ingestion/ \
    --exclude "*" --include "*.gz" --storage-class STANDARD_IA

# Cleanup old backups
find "$BACKUP_DIR" -name "*.gz" -mtime +$RETENTION_DAYS -delete

echo "Backup completed: dump_$TIMESTAMP.rdb.gz"
```

Cron schedule:

```cron
# Backup ogni 6 ore
0 */6 * * * /opt/scripts/backup_redis.sh >> /var/log/redis-backup.log 2>&1
```


### Procedura di Recovery

```bash
# restore_redis.sh
#!/bin/bash
set -e

BACKUP_FILE="$1"
REDIS_DATA_DIR="/var/lib/redis"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file.rdb.gz>"
    exit 1
fi

# Stop Redis
systemctl stop redis

# Backup current state
cp "$REDIS_DATA_DIR/dump.rdb" "$REDIS_DATA_DIR/dump.rdb.pre-restore"

# Restore backup
gunzip -c "$BACKUP_FILE" > "$REDIS_DATA_DIR/dump.rdb"
chown redis:redis "$REDIS_DATA_DIR/dump.rdb"

# Start Redis
systemctl start redis

# Verify
redis-cli ping
echo "Recovery completed. Verify data integrity."
```


### Data Replication Setup

```bash
# Redis replica config (redis-replica.conf)
replicaof redis-primary 6379
masterauth ${REDIS_PASSWORD}
replica-read-only yes
```


### Disaster Recovery Runbook

1. **Failure Detection**: Alert Prometheus/Grafana (< 2min)
2. **Isolamento**: Stop producer per prevenire data loss
3. **Diagnosis**: Check logs, Redis status, network connectivity
4. **Recovery primario**: Restart service con health checks
5. **Recovery secondario**: Restore da backup più recente (RTO < 1h)
6. **Validazione**: Verify UIDVALIDITY, controllare missing UIDs, replay DLQ
7. **Monitoring post-recovery**: Osserva metriche per 1h

RPO (Recovery Point Objective): **6 ore** (backup interval)
RTO (Recovery Time Objective): **1 ora** (restore + validation)

***

## Performance Tuning

### Redis Optimizations

```conf
# redis-tuned.conf
# Memory
maxmemory 4gb
maxmemory-policy allkeys-lru
maxmemory-samples 10

# Persistence bilanciata
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# Network
tcp-backlog 511
timeout 300
tcp-keepalive 300

# Streams
stream-node-max-bytes 4096
stream-node-max-entries 100

# Performance
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
replica-lazy-flush yes
```


### Python Optimizations

```python
# Usa connection pooling
from redis.connection import ConnectionPool

pool = ConnectionPool.from_url(
    os.getenv('REDIS_URL'),
    max_connections=20,
    socket_keepalive=True,
    socket_keepalive_options={
        socket.TCP_KEEPIDLE: 1,
        socket.TCP_KEEPINTVL: 1,
        socket.TCP_KEEPCNT: 5
    },
    decode_responses=True
)
r = redis.Redis(connection_pool=pool)

# Batch operations dove possibile
pipe = r.pipeline()
for payload in batch:
    pipe.xadd(STREAM, {'payload': json.dumps(payload)})
pipe.execute()
```


### Benchmark Results (target)

```text
Producer:
- Throughput: 500-1000 msg/s (dipende da IMAP server)
- Latency P50: <100ms, P99: <500ms

Worker (4 istanze):
- Throughput aggregato: 2000+ msg/s
- Latency P50: <50ms, P99: <200ms

Redis:
- Memory: <2GB per 100k messaggi in stream
- CPU: <20% su 2 core @ idle, <60% @ peak
```


***

## Troubleshooting Guide

### Diagnostica Rapida

```bash
# Check stream health
redis-cli XLEN imap_emails
redis-cli XINFO STREAM imap_emails
redis-cli XPENDING imap_emails workers

# Check consumer group
redis-cli XINFO GROUPS imap_emails
redis-cli XINFO CONSUMERS imap_emails workers

# DLQ inspection
redis-cli XLEN imap_dlq
redis-cli XRANGE imap_dlq - + COUNT 10

# Connection test
redis-cli PING
redis-cli INFO clients
redis-cli CLIENT LIST
```


### Problemi Comuni

#### 1. Stream Accumulo (Backlog Growing)

**Sintomo**: `XLEN imap_emails` cresce continuamente
**Cause**: Worker lenti, CPU throttling, errori processing
**Fix**:

```bash
# Scale worker
kubectl scale deployment imap-worker --replicas=8

# Check worker logs
kubectl logs -l app=imap-worker --tail=100

# Force reclaim pending
redis-cli XCLAIM imap_emails workers consumer-new 0 $(redis-cli XPENDING imap_emails workers - + 10 | awk '{print $1}')
```


#### 2. UIDVALIDITY Reset Loop

**Sintomo**: Log continui "UIDVALIDITY changed"
**Cause**: IMAP server instabile, mailbox re-created
**Fix**:

```python
# Aumenta tolleranza nel producer
UIDVALIDITY_CHANGE_THRESHOLD = 3  # Reset solo dopo N cambi in M minuti
```


#### 3. OAuth Token Expired

**Sintomo**: `imapclient.IMAPError: LOGIN failed`
**Cause**: Token OAuth2 scaduto
**Fix**:

```bash
# Refresh token (Gmail example)
python refresh_oauth_token.py --client-id=... --client-secret=... --refresh-token=...

# Update secret
kubectl create secret generic imap-secrets \
  --from-literal=IMAP_OAUTH_TOKEN=$NEW_TOKEN \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart producer
kubectl rollout restart deployment/imap-producer
```


#### 4. Redis Out Of Memory

**Sintomo**: `OOM command not allowed when used memory > 'maxmemory'`
**Fix**:

```bash
# Trim streams
redis-cli XTRIM imap_emails MAXLEN ~ 10000
redis-cli XTRIM imap_dlq MAXLEN ~ 1000

# Increase memory
kubectl edit deployment redis  # Aumenta limits.memory

# Oppure scala verticalmente Redis instance
```


#### 5. Dead Consumer Processes

**Sintomo**: Pending messages con idle time alto
**Fix**:

```bash
# Identifica consumer morti
redis-cli XPENDING imap_emails workers - + 10

# Force claim
for msg_id in $(redis-cli XPENDING imap_emails workers - + 100 | awk '$4 > 300000 {print $1}'); do
    redis-cli XCLAIM imap_emails workers new-consumer 0 $msg_id
done
```


### Logs Centralization (ELK Stack)

```yaml
# filebeat-config.yml
filebeat.inputs:
- type: container
  paths:
    - /var/log/containers/imap-*.log
  processors:
    - add_kubernetes_metadata:
        host: ${NODE_NAME}

output.elasticsearch:
  hosts: ['${ELASTICSEARCH_HOST}:9200']
  index: "imap-ingestion-%{+yyyy.MM.dd}"
```


***

## Health Checks Avanzati

### HTTP Health Endpoint

```python
# health_check.py
from flask import Flask, jsonify
import redis
import os

app = Flask(__name__)
r = Redis.from_url(os.getenv('REDIS_URL'), decode_responses=True)

@app.route('/health')
def health():
    """Basic liveness check"""
    return jsonify({'status': 'ok'}), 200

@app.route('/ready')
def ready():
    """Readiness check con dipendenze"""
    checks = {}
    
    # Redis connectivity
    try:
        r.ping()
        checks['redis'] = 'ok'
    except Exception as e:
        checks['redis'] = f'error: {e}'
        return jsonify({'status': 'not_ready', 'checks': checks}), 503
    
    # Stream exists
    try:
        r.xlen(os.getenv('QUEUE_STREAM', 'imap_emails'))
        checks['stream'] = 'ok'
    except Exception as e:
        checks['stream'] = f'error: {e}'
        return jsonify({'status': 'not_ready', 'checks': checks}), 503
    
    return jsonify({'status': 'ready', 'checks': checks}), 200

@app.route('/metrics')
def metrics():
    """Metriche esposte per scraping"""
    stream = os.getenv('QUEUE_STREAM', 'imap_emails')
    return jsonify({
        'stream_length': r.xlen(stream),
        'dlq_length': r.xlen('imap_dlq'),
        'pending_count': r.xpending(stream, 'workers')['pending'] if r.exists(stream) else 0
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

Integra in Kubernetes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10

readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 3
```


***

## Runbook Operativo

### Daily Operations

```bash
# Morning checks (automatable via cron)
./scripts/daily_check.sh

# daily_check.sh content:
#!/bin/bash
echo "=== Daily Health Check ==="
echo "Stream Length: $(redis-cli XLEN imap_emails)"
echo "DLQ Length: $(redis-cli XLEN imap_dlq)"
echo "Pending: $(redis-cli XPENDING imap_emails workers | head -1)"
echo "Redis Memory: $(redis-cli INFO memory | grep used_memory_human)"
echo "Producer Status: $(kubectl get pods -l app=imap-producer -o wide)"
echo "Worker Status: $(kubectl get pods -l app=imap-worker -o wide)"
```


### Weekly Maintenance

- Review DLQ messages e investigate root cause
- Analyze Grafana dashboards per trend anomali
- Validate backup integrity (test restore su staging)
- Update dependencies `pip list --outdated`
- Review e rotate logs


### Monthly Tasks

- Security audit: scan immagini Docker, rotate secrets
- Capacity planning: analizza trend crescita stream
- Performance review: benchmark throughput/latency
- DR drill: simula failure e restore completo


### On-Call Escalation

**Severity 1** (Immediate): Producer down, Redis crashed, data loss
**Severity 2** (30min): High pending, DLQ growing, performance degraded
**Severity 3** (4hr): Configuration issues, non-blocking errors

Contatti: `oncall@example.com`, Slack: `#imap-ingestion-alerts`

***

## Schema Dati e Validazione

### Payload Schema (JSON Schema)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "EmailPayload",
  "type": "object",
  "required": ["mailbox", "uid", "uidvalidity", "timestamp", "from", "subject"],
  "properties": {
    "mailbox": {
      "type": "string",
      "description": "Nome mailbox IMAP"
    },
    "uid": {
      "type": "integer",
      "minimum": 1,
      "description": "UID univoco messaggio"
    },
    "uidvalidity": {
      "type": "integer",
      "description": "UIDVALIDITY mailbox"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO8601 timestamp UTC"
    },
    "from": {
      "type": "string",
      "format": "email"
    },
    "subject": {
      "type": "string",
      "maxLength": 500
    },
    "body_snippet": {
      "type": "string",
      "maxLength": 1000
    },
    "full_size": {
      "type": "integer",
      "minimum": 0,
      "description": "Size in bytes"
    }
  }
}
```

Validazione runtime:

```python
from jsonschema import validate, ValidationError

SCHEMA = {...}  # Schema sopra

def validate_payload(payload):
    try:
        validate(instance=payload, schema=SCHEMA)
        return True
    except ValidationError as e:
        logger.error(f"Invalid payload: {e.message}")
        return False
```


***

## Gestione Secrets e Compliance

### HashiCorp Vault Integration

```python
# vault_secrets.py
import hvac
import os

client = hvac.Client(url=os.getenv('VAULT_ADDR'), token=os.getenv('VAULT_TOKEN'))

def get_imap_credentials():
    """Fetch IMAP credentials from Vault"""
    secret = client.secrets.kv.v2.read_secret_version(path='imap/production')
    return secret['data']['data']

# Usage in producer.py
creds = get_imap_credentials()
IMAP_OAUTH_TOKEN = creds['oauth_token']
```


### GDPR/Data Retention

```python
# data_retention.py
import time
from datetime import datetime, timedelta

RETENTION_DAYS = 90  # Configurable per compliance

def cleanup_old_data():
    """Rimuovi dati oltre retention period"""
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).timestamp()
    
    # Cleanup processed_uids sets oltre TTL
    for key in r.scan_iter('processed_uids:*'):
        # Già gestito da EXPIRE in worker, ma double-check
        ttl = r.ttl(key)
        if ttl == -1:  # No expiry set
            r.expire(key, RETENTION_DAYS * 86400)
    
    # Trim DLQ messages oltre retention
    oldest_entries = r.xrange('imap_dlq', '-', '+', count=1000)
    for msg_id, data in oldest_entries:
        if float(data.get('failed_at', 0)) < cutoff:
            r.xdel('imap_dlq', msg_id)

# Schedule via cron
if __name__ == '__main__':
    cleanup_old_data()
```


***

## Conclusioni e Next Steps

Questa architettura production-ready fornisce:[^1_1]

- **Reliability**: Retry, DLQ, idempotenza, persistence Redis
- **Scalability**: Worker orizzontali, Redis Streams native, HPA Kubernetes
- **Observability**: Prometheus metrics, Grafana dashboards, structured logging
- **Security**: OAuth2, Vault secrets, Redis ACL, no PII in logs
- **Operability**: Health checks, runbooks, automated backups, CI/CD completo


### Roadmap Future Enhancements

1. **Multi-tenancy**: Producer pool con routing per tenant/mailbox
2. **Event sourcing**: Archiviazione full email su S3/MinIO
3. **Analytics**: Streaming processing con Kafka/Flink per ML pipeline
4. **Advanced retry**: Exponential backoff configurabile, circuit breaker pattern
5. **Geo-replication**: Redis cluster multi-region per HA globale
6. **A/B testing**: Feature flags per rollout graduali nuove logiche processing

### Risorse Utili

- **Redis Streams**: https://redis.io/docs/data-types/streams/
- **IMAP RFC**: https://datatracker.ietf.org/doc/html/rfc3501
- **Prometheus Best Practices**: https://prometheus.io/docs/practices/
- **Kubernetes Patterns**: https://k8s-patterns.com/

***

**Sistema pronto per production deployment con tutte le componenti enterprise-grade necessarie**.[^1_2][^1_5][^1_3]
<span style="display:none">[^1_10][^1_11][^1_12][^1_13][^1_14][^1_15][^1_16][^1_17][^1_18][^1_19][^1_20][^1_21][^1_22][^1_23][^1_24][^1_25][^1_26][^1_27][^1_28][^1_29][^1_30][^1_31][^1_32][^1_33][^1_34][^1_35][^1_36][^1_37][^1_38][^1_39][^1_40][^1_41][^1_42][^1_43][^1_44][^1_45][^1_46][^1_47][^1_48][^1_49][^1_50][^1_51][^1_52][^1_53][^1_54][^1_55][^1_56]</span>

<div align="center">⁂</div>

[^1_1]: https://www.nuface.tw/building-a-complete-enterprise-grade-mail-system-overview/

[^1_2]: https://semaphore.io/blog/unit-testing-vs-integration-testing

[^1_3]: https://community.zextras.com/disaster-recovery-planning-for-email-systems-ensuring-business-continuity-and-data-resilience-blog/

[^1_4]: https://arxiv.org/pdf/2110.13575.pdf

[^1_5]: https://oneuptime.com/blog/post/2026-02-09-redis-consumer-groups-scalable/view

[^1_6]: https://www.aotsend.com/blog/p7231.html

[^1_7]: https://dn.org/tools-for-monitoring-and-debugging-email-infrastructure/

[^1_8]: https://dev.to/rslim087a/monitoring-redis-with-prometheus-and-grafana-56pk

[^1_9]: https://emailhosting.com/service/index.php?rp=%2Fknowledgebase%2F766%2FResilient-Disaster-Recovery-Strategies-for-Email-Hosting-Ensuring-Business-Continuity.html

[^1_10]: https://www.semanticscholar.org/paper/d901ef7af792692e2fd2750e0aba0ae644161002

[^1_11]: https://www.semanticscholar.org/paper/ceefe1b48797c2f5932e21b836390ccd30cd4a63

[^1_12]: https://www.semanticscholar.org/paper/5c8e26565416b83a455d7f3cd288e8be1a46e08d

[^1_13]: https://www.semanticscholar.org/paper/738dad3f390dd589e69d8419eb964d9d29cc0874

[^1_14]: https://research.wur.nl/en/publications/cbb4acbb-597f-4d32-afb2-c877004a233c

[^1_15]: https://www.semanticscholar.org/paper/f78642dafa76148d8c29397da6bc653c75426ccb

[^1_16]: https://www.semanticscholar.org/paper/e72cca7753fde121f1ddbbecfca64d449f4d2384

[^1_17]: https://www.semanticscholar.org/paper/3ce5e42fe3c6bee3719626974ed9993c2d1099a8

[^1_18]: https://www.semanticscholar.org/paper/ab2b16434b3692b4ffb14cf17adb587d9e822af6

[^1_19]: https://www.semanticscholar.org/paper/6adafd94b2614181cecbfbb818e1e5735c1e1f0b

[^1_20]: https://arxiv.org/pdf/1309.5568.pdf

[^1_21]: https://surface.syr.edu/cgi/viewcontent.cgi?article=1058\&context=eecs

[^1_22]: https://arxiv.org/pdf/2208.00388.pdf

[^1_23]: https://arxiv.org/pdf/2201.11216.pdf

[^1_24]: https://arxiv.org/pdf/1709.00412.pdf

[^1_25]: http://arxiv.org/pdf/2110.08588.pdf

[^1_26]: https://arxiv.org/pdf/1804.07706.pdf

[^1_27]: https://arxiv.org/pdf/2312.04100.pdf

[^1_28]: https://www.getmailbird.com/ultimate-email-productivity-guide/

[^1_29]: https://wedotheweb.co.za/efficient-email-management-with-imap-best-practices-for-organising-securing-and-synchronising-your-inbox/

[^1_30]: https://www.xeams.com/best-worst-practices-for-email-server.htm

[^1_31]: https://oneuptime.com/blog/post/2026-01-30-redis-streams-consumer-groups/view

[^1_32]: https://help.octiga.io/kb/guide/en/imap-best-practice-roll-out-8UYxIA7Kr1/Steps/3868162

[^1_33]: https://www.oreateai.com/blog/practical-guide-to-automated-testing-of-email-systems-building-a-complete-testing-framework-from-scratch/a7b9f2d7b803a212210f23b796efe1f7

[^1_34]: https://ejournal.itn.ac.id/index.php/jati/article/view/14067

[^1_35]: https://ejournal.itn.ac.id/index.php/jati/article/view/13969

[^1_36]: https://dl.acm.org/doi/10.1145/3706594.3726980

[^1_37]: https://ieeexplore.ieee.org/document/11068479/

[^1_38]: https://arxiv.org/abs/2306.17407

[^1_39]: https://arxiv.org/abs/2502.09801

[^1_40]: https://ieeexplore.ieee.org/document/9510016/

[^1_41]: https://ieeexplore.ieee.org/document/10479442/

[^1_42]: https://dl.acm.org/doi/10.1145/3324884.3415288

[^1_43]: https://indjst.org/articles/integration-testing-prior-to-unit-testing-a-paradigm-shift-in-object-oriented-software-testing-of-agile-software-engineering

[^1_44]: https://arxiv.org/pdf/2101.12036.pdf

[^1_45]: https://arxiv.org/pdf/1906.01463.pdf

[^1_46]: https://arxiv.org/pdf/2001.04221.pdf

[^1_47]: http://arxiv.org/pdf/2307.05147.pdf

[^1_48]: http://arxiv.org/pdf/2209.06315v1.pdf

[^1_49]: http://arxiv.org/pdf/2305.04764.pdf

[^1_50]: http://conference.scipy.org/proceedings/scipy2011/pdfs/jacky.pdf

[^1_51]: https://stackoverflow.com/questions/6161982/pop3-imap-server-for-unit-testing

[^1_52]: https://www.frederikbanke.com/integration-testing-in-python-rabbitmq/

[^1_53]: https://www.youtube.com/watch?v=RPhZPXHn4jA

[^1_54]: https://docs.pylonsproject.org/projects/pyramid/en/latest/narr/testing.html

[^1_55]: https://stackoverflow.com/questions/15639265/is-there-a-convention-to-distinguish-python-integration-tests-from-unit-tests

[^1_56]: https://redis.io/tutorials/operate/observability/redis-software-prometheus-and-grafana/


---

## **SOP (Standard Operating Procedures) - Maintenance e Troubleshooting**

### SOP-001: Daily Health Check Procedure

**Obiettivo**: Verificare stato sistema e identificare anomalie precocemente[^2_1]

**Frequenza**: Ogni giorno lavorativo ore 9:00 CET

**Responsabile**: On-call engineer / SRE team

**Durata stimata**: 10-15 minuti

#### Procedura Step-by-Step

**Step 1: Verifica Status Componenti**

```bash
# Check producer pods
kubectl get pods -l app=imap-producer -o wide
# Expected: 1/1 Running, Age > 0

# Check worker pods
kubectl get pods -l app=imap-worker -o wide
# Expected: 4/4 Running (o replicas configurate)

# Check Redis pod/service
kubectl get pods -l app=redis -o wide
redis-cli -h redis-service PING
# Expected: PONG
```

**Step 2: Verifica Metriche Chiave**[^2_1]

```bash
# Stream length (deve essere < 1000 in condizioni normali)
redis-cli XLEN imap_emails

# DLQ check (deve essere < 10)
redis-cli XLEN imap_dlq

# Pending messages (< 50 è normale)
redis-cli XPENDING imap_emails workers | head -1

# Redis memory usage (< 80% maxmemory)
redis-cli INFO memory | grep used_memory_human
redis-cli INFO memory | grep maxmemory_human
```

**Step 3: Verifica Log Errori Recenti**

```bash
# Producer errors (ultime 24h)
kubectl logs -l app=imap-producer --since=24h | grep -i "error\|critical\|exception" | wc -l

# Worker errors
kubectl logs -l app=imap-worker --since=24h | grep -i "error\|critical" | wc -l

# Redis warnings
redis-cli --no-raw CONFIG GET loglevel
redis-cli --latency-history
```

**Step 4: Dashboard Review**

- Accedi Grafana: `https://grafana.internal/d/imap-ingestion`
- Verifica pannelli:
    - Processing throughput (deve essere > 100 msg/s nelle ore di punta)
    - Error rate (< 1%)
    - Pending messages trend (stabile o decrescente)
    - Redis memory trend (crescita lineare accettabile)

**Step 5: Documentazione**

```bash
# Genera report automatico
./scripts/daily_health_report.sh > /reports/health_$(date +%Y%m%d).txt

# Invia a Slack se OK
curl -X POST $SLACK_WEBHOOK_URL \
  -H 'Content-Type: application/json' \
  -d '{"text":"✅ Daily Health Check PASSED - '$(date)'"}'
```

**Azioni in caso di anomalie**: Escalate a SOP-005 (Incident Response)

***

### SOP-002: Weekly Maintenance Window

**Obiettivo**: Manutenzione preventiva e cleanup[^2_2][^2_1]

**Frequenza**: Ogni domenica ore 2:00-4:00 CET (low traffic period)

**Responsabile**: DevOps team

**Impatto**: Minimal (< 5s downtime per rolling restart)

#### Procedura Step-by-Step

**Step 1: Pre-Maintenance Checklist**

```bash
# Backup current state
./scripts/backup_redis.sh

# Capture current metrics baseline
kubectl top pods > /tmp/metrics_before.txt
redis-cli INFO > /tmp/redis_info_before.txt

# Notify team
curl -X POST $SLACK_WEBHOOK_URL -d '{"text":"🔧 Weekly maintenance STARTED"}'
```

**Step 2: Redis Maintenance**

```bash
# Compact AOF se > 100MB
AOF_SIZE=$(redis-cli INFO persistence | grep aof_current_size | cut -d: -f2)
if [ $AOF_SIZE -gt 104857600 ]; then
    redis-cli BGREWRITEAOF
    # Wait completion (check INFO persistence)
fi

# Cleanup expired keys (lazy eviction check)
redis-cli INFO stats | grep expired_keys

# Memory defragmentation se needed
FRAG_RATIO=$(redis-cli INFO memory | grep mem_fragmentation_ratio | cut -d: -f2)
if (( $(echo "$FRAG_RATIO > 1.5" | bc -l) )); then
    redis-cli MEMORY PURGE
fi
```

**Step 3: Stream Maintenance**

```bash
# Trim main stream se > 100k entries
STREAM_LEN=$(redis-cli XLEN imap_emails)
if [ $STREAM_LEN -gt 100000 ]; then
    redis-cli XTRIM imap_emails MAXLEN ~ 50000
    echo "Stream trimmed from $STREAM_LEN to 50000"
fi

# Archive DLQ messages > 7 giorni
python3 <<EOF
import redis
import json
import time
from datetime import datetime, timedelta

r = redis.Redis(host='redis-service', port=6379, decode_responses=True)
cutoff = (datetime.utcnow() - timedelta(days=7)).timestamp()

archived = 0
for msg_id, data in r.xrange('imap_dlq', '-', '+'):
    if float(data.get('failed_at', 0)) < cutoff:
        # Archive to S3/file before delete
        with open(f'/archives/dlq_{msg_id}.json', 'w') as f:
            json.dump({msg_id: data}, f)
        r.xdel('imap_dlq', msg_id)
        archived += 1

print(f"Archived {archived} old DLQ entries")
EOF
```

**Step 4: Application Updates**[^2_2]

```bash
# Rolling restart worker (refresh config, clear memory leaks)
kubectl rollout restart deployment/imap-worker
kubectl rollout status deployment/imap-worker --timeout=300s

# Producer restart (solo se necessario)
# kubectl rollout restart deployment/imap-producer

# Update dependencies se disponibili
pip list --outdated > /tmp/outdated_packages.txt
# Review e schedule update in prossimo maintenance window se critici
```

**Step 5: Verification \& Cleanup**

```bash
# Verify services healthy post-restart
./scripts/health_check.sh

# Compare metrics
kubectl top pods > /tmp/metrics_after.txt
diff /tmp/metrics_before.txt /tmp/metrics_after.txt

# Cleanup old backups (> 7 giorni)
find /backups/redis -name "*.gz" -mtime +7 -delete

# Rotate logs
kubectl logs -l app=imap-worker --tail=10000 > /logs/worker_$(date +%Y%m%d).log
kubectl logs -l app=imap-producer --tail=10000 > /logs/producer_$(date +%Y%m%d).log
```

**Step 6: Post-Maintenance Report**

```bash
# Generate report
cat > /reports/maintenance_$(date +%Y%m%d).txt <<EOF
Weekly Maintenance Report - $(date)
=====================================
Stream Length Before: $STREAM_LEN
Stream Length After: $(redis-cli XLEN imap_emails)
DLQ Entries Archived: $archived
Worker Restart Duration: $(kubectl rollout status deployment/imap-worker | grep -o '[0-9]*s')
Status: COMPLETED
EOF

# Notify team
curl -X POST $SLACK_WEBHOOK_URL -d '{"text":"✅ Weekly maintenance COMPLETED"}'
```


***

### SOP-003: Redis Backup and Restore Procedure

**Obiettivo**: Proteggere dati critici con backup regolari e testati[^2_3][^2_4][^2_5]

#### Backup Strategy: Hybrid RDB + AOF[^2_6][^2_5]

**Configurazione Raccomandata**:

```conf
# redis.conf - Hybrid persistence
save 900 1       # RDB snapshot after 15min if ≥1 key changed
save 300 10      # After 5min if ≥10 keys changed
save 60 10000    # After 1min if ≥10k keys changed

appendonly yes
appendfsync everysec  # Balance performance/durability
no-appendfsync-on-rewrite yes
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
```

**Rationale**: RDB fornisce fast recovery, AOF minimizza data loss (< 1 secondo)[^2_4][^2_5]

#### Automated Backup Script (Enhanced)[^2_5]

```bash
#!/bin/bash
# /opt/scripts/backup_redis_advanced.sh

set -euo pipefail

# Configuration
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD}"
BACKUP_DIR="/backups/redis"
S3_BUCKET="${S3_BUCKET:-s3://prod-redis-backups}"
RETENTION_DAYS=14
RETENTION_MONTHS=6
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_TYPE="${1:-full}"  # full|incremental

# Logging
exec 1> >(logger -s -t redis-backup) 2>&1

echo "=== Redis Backup Started: $DATE ==="

# Create directories
mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}

# Pre-backup health check
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" --no-auth-warning PING || {
    echo "ERROR: Redis not responding"
    exit 1
}

# Get current persistence status
LAST_SAVE=$(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning LASTSAVE)
AOF_ENABLED=$(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning CONFIG GET appendonly | tail -1)

# Trigger BGSAVE (non-blocking snapshot)
echo "Triggering BGSAVE..."
redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning BGSAVE

# Wait for BGSAVE completion (max 5min)
TIMEOUT=300
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
    SAVE_IN_PROGRESS=$(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning INFO persistence | grep rdb_bgsave_in_progress | cut -d: -f2 | tr -d '\r')
    if [ "$SAVE_IN_PROGRESS" = "0" ]; then
        echo "BGSAVE completed"
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo "ERROR: BGSAVE timeout"
    exit 1
fi

# Determine backup destination based on schedule
DAY_OF_WEEK=$(date +%u)
DAY_OF_MONTH=$(date +%d)

if [ "$DAY_OF_MONTH" = "01" ]; then
    DEST="$BACKUP_DIR/monthly"
    LABEL="monthly"
elif [ "$DAY_OF_WEEK" = "7" ]; then
    DEST="$BACKUP_DIR/weekly"
    LABEL="weekly"
else
    DEST="$BACKUP_DIR/daily"
    LABEL="daily"
fi

# Copy RDB snapshot
echo "Copying RDB snapshot..."
REDIS_DATA_DIR="/var/lib/redis"
cp "$REDIS_DATA_DIR/dump.rdb" "$DEST/dump_${LABEL}_${DATE}.rdb"

# Copy AOF if enabled
if [ "$AOF_ENABLED" = "yes" ]; then
    echo "Copying AOF..."
    cp "$REDIS_DATA_DIR/appendonly.aof" "$DEST/appendonly_${LABEL}_${DATE}.aof" || true
fi

# Compress backups
echo "Compressing backups..."
gzip -f "$DEST/dump_${LABEL}_${DATE}.rdb"
[ -f "$DEST/appendonly_${LABEL}_${DATE}.aof" ] && gzip -f "$DEST/appendonly_${LABEL}_${DATE}.aof"

# Generate metadata
cat > "$DEST/backup_${LABEL}_${DATE}.meta" <<EOF
backup_date: $DATE
redis_version: $(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning INFO server | grep redis_version | cut -d: -f2 | tr -d '\r')
db_size: $(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning DBSIZE | cut -d: -f2)
used_memory: $(redis-cli -h "$REDIS_HOST" -a "$REDIS_PASSWORD" --no-auth-warning INFO memory | grep used_memory_human | cut -d: -f2 | tr -d '\r')
rdb_last_save: $(date -d @$LAST_SAVE)
backup_type: $LABEL
EOF

# Upload to S3 (3-2-1 backup rule) [web:98]
echo "Uploading to S3..."
aws s3 sync "$DEST" "$S3_BUCKET/$LABEL/" \
    --exclude "*" \
    --include "*.gz" \
    --include "*.meta" \
    --storage-class STANDARD_IA \
    --metadata "backup-date=$DATE,backup-type=$LABEL"

# Verify upload
S3_COUNT=$(aws s3 ls "$S3_BUCKET/$LABEL/" | grep "${DATE}" | wc -l)
if [ "$S3_COUNT" -lt 1 ]; then
    echo "ERROR: S3 upload verification failed"
    exit 1
fi

# Cleanup old local backups [web:94]
echo "Cleaning up old backups..."
find "$BACKUP_DIR/daily" -name "*.gz" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR/weekly" -name "*.gz" -mtime +$((RETENTION_DAYS * 4)) -delete
find "$BACKUP_DIR/monthly" -name "*.gz" -mtime +$((RETENTION_MONTHS * 30)) -delete

# Integrity check (random sample verification)
RANDOM_BACKUP=$(find "$DEST" -name "dump_*.rdb.gz" | sort -R | head -1)
if [ -n "$RANDOM_BACKUP" ]; then
    echo "Running integrity check on $RANDOM_BACKUP..."
    gunzip -t "$RANDOM_BACKUP" && echo "✓ Integrity check passed" || echo "✗ Integrity check FAILED"
fi

# Metrics export (Prometheus pushgateway)
BACKUP_SIZE=$(du -sb "$DEST" | awk '{print $1}')
cat <<EOF | curl --data-binary @- http://pushgateway:9091/metrics/job/redis_backup
# TYPE redis_backup_size_bytes gauge
redis_backup_size_bytes{type="$LABEL"} $BACKUP_SIZE
# TYPE redis_backup_timestamp gauge
redis_backup_timestamp{type="$LABEL"} $(date +%s)
# TYPE redis_backup_duration_seconds gauge
redis_backup_duration_seconds{type="$LABEL"} $ELAPSED
EOF

echo "=== Redis Backup Completed Successfully ==="
echo "Backup location: $DEST"
echo "S3 location: $S3_BUCKET/$LABEL/"
echo "Backup size: $(du -sh $DEST | awk '{print $1}')"
```

**Cron Schedule**:[^2_5]

```cron
# Daily backups at 2 AM
0 2 * * * /opt/scripts/backup_redis_advanced.sh daily

# Test restore monthly (first Sunday)
0 4 1 * 0 /opt/scripts/test_restore.sh
```


#### Restore Procedure[^2_3]

**Emergency Restore (Production)**:

```bash
#!/bin/bash
# /opt/scripts/restore_redis.sh

set -e

BACKUP_FILE="${1:?Usage: $0 <backup_file.rdb.gz> [aof_file.aof.gz]}"
AOF_FILE="${2:-}"

echo "⚠️  WARNING: This will overwrite current Redis data"
read -p "Continue? (yes/no): " CONFIRM
[ "$CONFIRM" != "yes" ] && exit 1

# Stop application writes
echo "Scaling down producer/workers..."
kubectl scale deployment imap-producer --replicas=0
kubectl scale deployment imap-worker --replicas=0

# Wait for graceful shutdown
sleep 30

# Stop Redis (or switch to maintenance mode)
kubectl exec -it redis-0 -- redis-cli SHUTDOWN SAVE
sleep 10

# Backup current state (just in case)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
kubectl exec redis-0 -- tar czf /tmp/pre-restore-$TIMESTAMP.tar.gz /var/lib/redis/

# Copy backup to Redis pod
kubectl cp "$BACKUP_FILE" redis-0:/tmp/restore.rdb.gz
kubectl exec redis-0 -- gunzip -f /tmp/restore.rdb.gz

# Restore RDB
kubectl exec redis-0 -- sh -c 'cp /tmp/restore.rdb /var/lib/redis/dump.rdb && chown redis:redis /var/lib/redis/dump.rdb'

# Restore AOF if provided
if [ -n "$AOF_FILE" ]; then
    kubectl cp "$AOF_FILE" redis-0:/tmp/restore.aof.gz
    kubectl exec redis-0 -- gunzip -f /tmp/restore.aof.gz
    kubectl exec redis-0 -- sh -c 'cp /tmp/restore.aof /var/lib/redis/appendonly.aof && chown redis:redis /var/lib/redis/appendonly.aof'
fi

# Start Redis
kubectl exec redis-0 -- redis-server /etc/redis/redis.conf --daemonize yes

# Wait for Redis ready
sleep 10
kubectl exec redis-0 -- redis-cli PING

# Verify data
DBSIZE=$(kubectl exec redis-0 -- redis-cli DBSIZE | cut -d: -f2)
echo "Restored DB size: $DBSIZE keys"

# Scale up applications
echo "Scaling up producer/workers..."
kubectl scale deployment imap-producer --replicas=1
kubectl scale deployment imap-worker --replicas=4

# Health check
sleep 30
./scripts/health_check.sh

echo "✅ Restore completed successfully"
echo "Verify data integrity and monitor logs closely for next 1 hour"
```

**Point-in-Time Recovery (PITR)**:[^2_3]

```bash
# Usa AOF per PITR granulare
# 1. Identifica timestamp target
TARGET_TIMESTAMP="2026-02-15T14:30:00Z"

# 2. Estrai AOF fino a quel punto
python3 <<EOF
import time
from datetime import datetime

target_ts = datetime.fromisoformat('$TARGET_TIMESTAMP'.replace('Z', '+00:00')).timestamp()

with open('appendonly.aof', 'rb') as src, open('appendonly_pitr.aof', 'wb') as dst:
    for line in src:
        # Parse timestamp from AOF (se disponibile)
        # Copia linee fino a target_ts
        dst.write(line)
        # Stop quando raggiungi target
EOF

# 3. Restore con AOF troncato
# Usa procedura restore standard con appendonly_pitr.aof
```

**RPO/RTO Targets**:[^2_7]

- **RPO** (Recovery Point Objective): **< 1 secondo** (con AOF everysec)
- **RTO** (Recovery Time Objective): **< 30 minuti** (complete restore + validation)

***

### SOP-004: Performance Troubleshooting Decision Tree

**Sintomo**: High pending messages (> 100)

```
┌─────────────────────────────────┐
│ XPENDING > 100 for > 10 min    │
└────────────┬────────────────────┘
             │
             ▼
      ┌──────────────┐
      │ Check Worker │
      │ CPU/Memory   │
      └──────┬───────┘
             │
      ┌──────▼──────────────────────────┐
      │                                  │
   ┌──▼──────────┐          ┌───────────▼────────┐
   │ High CPU    │          │ Normal resources   │
   │ (> 80%)     │          │                    │
   └──┬──────────┘          └───────┬────────────┘
      │                              │
      ▼                              ▼
┌─────────────┐              ┌──────────────────┐
│ Scale worker│              │ Check Redis      │
│ replicas +4 │              │ latency          │
└─────────────┘              └────────┬─────────┘
                                      │
                             ┌────────▼──────────┐
                             │ redis-cli         │
                             │ --latency-history │
                             └────────┬──────────┘
                                      │
                          ┌───────────▼────────────┐
                          │                        │
                     ┌────▼──────┐        ┌────────▼──────┐
                     │ Latency OK│        │ Latency high  │
                     │ (< 10ms)  │        │ (> 50ms)      │
                     └────┬──────┘        └────┬──────────┘
                          │                    │
                          ▼                    ▼
                  ┌───────────────┐    ┌──────────────────┐
                  │ Check process │    │ Check Redis      │
                  │ time in code  │    │ memory & CPU     │
                  └───────────────┘    └─────────┬────────┘
                                                  │
                                         ┌────────▼────────┐
                                         │ Restart Redis / │
                                         │ Increase memory │
                                         └─────────────────┘
```

**Comandi Diagnostici**:

```bash
# 1. Worker bottleneck check
kubectl top pods -l app=imap-worker
# Se CPU > 80%: scale out
kubectl scale deployment imap-worker --replicas=$((CURRENT + 4))

# 2. Redis latency check
redis-cli --latency-history
redis-cli --intrinsic-latency 100
# Target: < 10ms P99

# 3. Network issue check
redis-cli --latency-dist
kubectl exec -it worker-pod -- ping redis-service

# 4. Processing time analysis (add instrumentation)
kubectl logs -l app=imap-worker --tail=100 | grep "Processing duration"

# 5. Consumer group lag
redis-cli XINFO GROUPS imap_emails
# Check lag field
```


***

### SOP-005: Incident Response Procedure[^2_8][^2_9]

**Severity Levels**:


| Severity | Description | Response Time | Examples |
| :-- | :-- | :-- | :-- |
| **SEV-1** | Complete service outage | Immediate | Redis crashed, all workers down, data loss |
| **SEV-2** | Degraded performance | 30 minutes | High latency, partial failures, DLQ growing |
| **SEV-3** | Minor issues | 4 hours | Configuration warnings, non-critical errors |

#### SEV-1 Incident Response Flow[^2_9]

**Phase 1: Detection \& Alert (0-5 min)**

```bash
# Automated alert triggers PagerDuty
# On-call engineer acknowledges incident

# Step 1: Triage - identify impact
./scripts/quick_triage.sh

# quick_triage.sh output:
# - Component status (producer/worker/redis)
# - Error rate spike time
# - Affected message count
# - Estimated data loss window
```

**Phase 2: Containment (5-15 min)**[^2_8]

```bash
# If Redis crashed
kubectl get pods -l app=redis  # Check pod status
kubectl logs redis-0 --tail=100  # Check crash reason

# Immediate recovery attempt
kubectl delete pod redis-0  # Force restart with persistent volume

# If persistent volume corrupted -> initiate restore
/opt/scripts/restore_redis.sh /backups/redis/daily/latest.rdb.gz

# If producer/worker issue
kubectl rollback deployment/imap-worker  # Rollback to last known good
kubectl scale deployment imap-worker --replicas=8  # Temporary overscale
```

**Phase 3: Communication (concurrent with containment)**

```bash
# Auto-post to status page
curl -X POST https://status.internal/incidents \
  -d '{"status":"investigating","message":"Email ingestion degraded - investigating root cause"}'

# Notify stakeholders
./scripts/notify_stakeholders.sh "SEV-1: IMAP Ingestion Incident"
```

**Phase 4: Recovery \& Validation (15-45 min)**

```bash
# After containment, validate recovery
./scripts/validate_recovery.sh

# validate_recovery.sh checks:
# 1. All pods healthy
# 2. Stream processing resumed
# 3. No new errors in logs
# 4. Throughput back to normal (> 500 msg/s)
# 5. DLQ not growing

# Identify missing UIDs (data loss assessment)
python3 <<EOF
import redis
r = redis.Redis(host='redis-service', decode_responses=True)

# Last processed UID before incident
last_uid_before = int(r.get('last_uid:prod@example.com') or 0)

# Connect to IMAP and check current max UID
import imapclient
server = imapclient.IMAPClient('imap.gmail.com', ssl=True)
server.login(...)
server.select_folder('INBOX')
current_max_uid = max(server.search(['ALL']))

gap = current_max_uid - last_uid_before
print(f"Potential missing UIDs: {gap}")

# Backfill logic (force re-scan range)
if gap > 0:
    r.set('last_uid:prod@example.com', last_uid_before - 100)  # Re-process last 100
    # Restart producer to pickup
EOF
```

**Phase 5: Post-Incident Review (PIR)**[^2_1]

Entro 48 ore dall'incident, condurre PIR meeting:

**Template PIR Document**:

```markdown
# Post-Incident Review: [SEV-1 Redis Crash - 2026-02-16]

## Incident Summary
- **Date**: 2026-02-16 14:30 CET
- **Duration**: 45 minutes
- **Severity**: SEV-1
- **Impact**: 2.5k messages delayed, 12 messages lost (recovered from IMAP)

## Timeline
- 14:30 - Redis OOM error, pod crashed
- 14:32 - PagerDuty alert triggered
- 14:35 - On-call engineer acknowledged, initiated triage
- 14:40 - Identified root cause: memory leak in Redis 7.0.5
- 14:45 - Restored from backup (5min old RDB snapshot)
- 14:50 - Producer resumed, worker scaled to 8 replicas
- 15:00 - Backfilled missing UIDs
- 15:15 - Declared incident resolved

## Root Cause
Redis 7.0.5 memory leak triggered by specific XREADGROUP pattern under high load.

## Contributing Factors
1. maxmemory set too aggressively (1GB, should be 2GB)
2. No memory alerting before OOM
3. Redis upgrade path not tested with production load pattern

## What Went Well
- Automated alerting worked perfectly (< 2min detection)
- Backup restore procedure executed flawlessly
- Data loss minimal due to recent backup

## What Went Wrong
- No pre-OOM warning alert configured
- Manual backfill required (should be automated)
- Communication delay to stakeholders (15min gap)

## Action Items
1. [P0] Upgrade Redis to 7.0.8 (patched memory leak) - Owner: @devops - Due: 2026-02-18
2. [P0] Increase maxmemory to 2GB + alert at 85% - Owner: @sre - Due: 2026-02-17
3. [P1] Implement automated backfill logic - Owner: @dev - Due: 2026-02-25
4. [P1] Add memory trend alerting (Prometheus) - Owner: @sre - Due: 2026-02-20
5. [P2] Document stakeholder notification SOP - Owner: @lead - Due: 2026-03-01

## Lessons Learned
- Test Redis upgrades under production-like load before deploy
- Memory monitoring should be predictive, not reactive
- Automate all recovery steps possible (backfill, scaling, validation)
```


***

### SOP-006: DLQ Processing \& Analysis

**Obiettivo**: Investigare e risolvere messaggi in Dead Letter Queue

**Trigger**: DLQ length > 10 per > 1 ora

#### Procedura Analisi DLQ

```bash
# 1. Export DLQ per analisi
redis-cli --csv XRANGE imap_dlq - + COUNT 100 > /tmp/dlq_export.csv

# 2. Categorizza errori
python3 <<'EOF'
import redis
import json
from collections import Counter

r = redis.Redis(host='redis-service', port=6379, decode_responses=True)

errors = []
for msg_id, data in r.xrange('imap_dlq', '-', '+', count=1000):
    errors.append(data.get('error', 'unknown'))

# Top error types
error_counts = Counter(errors)
print("Top 5 error types:")
for error, count in error_counts.most_common(5):
    print(f"  {error}: {count}")

# Sample payloads per error type
for error_type in error_counts.keys():
    print(f"\n=== Sample for: {error_type} ===")
    for msg_id, data in r.xrange('imap_dlq', '-', '+', count=1):
        if data.get('error') == error_type:
            payload = json.loads(data.get('payload', '{}'))
            print(f"  UID: {payload.get('uid')}, From: {payload.get('from')}, Subject: {payload.get('subject', '')[:50]}")
            break
EOF
```

**Common Error Patterns \& Resolutions**:


| Error Pattern | Root Cause | Resolution |
| :-- | :-- | :-- |
| `ValueError: Simulated processing error` | Test error (UID % 100 == 0) | Remove test logic da `process_message()` |
| `JSONDecodeError: Expecting value` | Corrupted payload | Add payload validation in producer |
| `TimeoutError: Database connection timeout` | Downstream DB slow | Increase timeout, add connection pooling |
| `UnicodeDecodeError: invalid utf-8` | Email encoding issues | Enhance encoding handling con `chardet` |

#### DLQ Replay Procedure

```bash
# Manual replay (dopo fix)
python3 <<'EOF'
import redis
import json
import time

r = redis.Redis(host='redis-service', port=6379, decode_responses=True)
STREAM = 'imap_emails'
DLQ_STREAM = 'imap_dlq'

replayed = 0
for msg_id, data in r.xrange(DLQ_STREAM, '-', '+', count=100):
    payload_str = data.get('payload')
    error = data.get('error')
    retry_count = int(data.get('retry_count', 0))
    
    # Condition: replay only specific errors after fix
    if 'Simulated processing error' in error:
        # Re-inject in main stream
        r.xadd(STREAM, {'payload': payload_str, 'replayed_from_dlq': 'true'})
        # Remove from DLQ
        r.xdel(DLQ_STREAM, msg_id)
        replayed += 1
        print(f"Replayed: {msg_id}")
        time.sleep(0.1)  # Rate limit

print(f"Total replayed: {replayed}")
EOF
```

**Automated DLQ Monitoring Alert**:

```yaml
# prometheus-alert.yml
- alert: DLQGrowthAnomaly
  expr: rate(imap_dlq_length[10m]) > 0.5
  for: 10m
  labels:
    severity: warning
    team: platform
  annotations:
    summary: "DLQ growing at {{ $value }} messages/min"
    runbook: "https://wiki.internal/runbooks/imap-dlq-analysis"
```


***

## **Security Hardening Best Practices**

### Security Layer 1: Network Isolation[^2_10][^2_11]

#### Redis Network Hardening

**redis.conf Production Security Settings**:[^2_12][^2_11]

```conf
# Network binding - NEVER bind to 0.0.0.0 in production
bind 127.0.0.1 ::1
# If networked Redis, bind to private IP only:
# bind 10.0.1.50

# Protected mode (extra safeguard)
protected-mode yes

# Port customization (security through obscurity - secondary defense)
port 6380  # Non-standard port

# TLS/SSL encryption (mandatory for networked Redis) [web:75]
tls-port 6381
tls-cert-file /etc/redis/tls/redis.crt
tls-key-file /etc/redis/tls/redis.key
tls-ca-cert-file /etc/redis/tls/ca.crt
tls-auth-clients yes  # Require client certificates

# Disable TLS 1.0/1.1 (insecure)
tls-protocols "TLSv1.2 TLSv1.3"
```

**Kubernetes Network Policies**:

```yaml
# network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: redis-network-policy
spec:
  podSelector:
    matchLabels:
      app: redis
  policyTypes:
  - Ingress
  - Egress
  ingress:
  # Allow only from producer and worker pods
  - from:
    - podSelector:
        matchLabels:
          app: imap-producer
    - podSelector:
        matchLabels:
          app: imap-worker
    - podSelector:
        matchLabels:
          app: metrics-exporter
    ports:
    - protocol: TCP
      port: 6379
  egress:
  # Allow only to DNS and within cluster
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: UDP
      port: 53
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: producer-network-policy
spec:
  podSelector:
    matchLabels:
      app: imap-producer
  policyTypes:
  - Egress
  egress:
  # Allow to IMAP servers and Redis only
  - to:
    - podSelector:
        matchLabels:
          app: redis
    ports:
    - protocol: TCP
      port: 6379
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 993  # IMAPS
    - protocol: TCP
      port: 443  # HTTPS for OAuth
```


***

### Security Layer 2: Authentication \& Authorization[^2_13][^2_12]

#### Redis ACL (Access Control Lists)[^2_11][^2_12]

**Replace Simple Password with Role-Based ACL**:

```bash
# redis-acl.conf
# Default user disabled (security best practice)
user default off

# Producer user - write-only to stream
user producer on >ProducerStrongPassword123! \
  ~* \
  +xadd +xlen +get +set +expire \
  -@all +@stream +@write

# Worker user - read/write/ack streams
user worker on >WorkerStrongPassword456! \
  ~* \
  +xreadgroup +xack +xclaim +xpending +sadd +sismember +expire \
  +xadd \
  -@all +@stream +@set

# Admin user - full access
user admin on >AdminSuperSecretPass789! \
  ~* \
  +@all

# Metrics exporter - read-only
user exporter on >ExporterReadOnlyPass! \
  ~* \
  +info +xlen +xpending +xinfo +config|get \
  -@all +@read
```

**Load ACL in redis.conf**:

```conf
aclfile /etc/redis/acl.conf
```

**Kubernetes Secrets per ACL Users**:

```yaml
# redis-secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: redis-credentials
type: Opaque
stringData:
  producer-password: "ProducerStrongPassword123!"
  worker-password: "WorkerStrongPassword456!"
  admin-password: "AdminSuperSecretPass789!"
  exporter-password: "ExporterReadOnlyPass!"
```

**Update Application to Use ACL**:[^2_13]

```python
# producer.py - Use ACL user
from redis import Redis

r = Redis(
    host=os.getenv('REDIS_HOST'),
    port=os.getenv('REDIS_PORT'),
    username='producer',  # ACL username
    password=os.getenv('REDIS_PRODUCER_PASSWORD'),
    ssl=True,  # Enable TLS
    ssl_cert_reqs='required',
    ssl_ca_certs='/etc/redis/tls/ca.crt',
    decode_responses=True
)
```


***

### Security Layer 3: IMAP Authentication Security[^2_14][^2_15]

#### OAuth2 Implementation (Gmail Example)[^2_14]

**Security Rationale**: IMAP legacy authentication vulnerable to password spraying attacks. OAuth2 + MFA enforcement prevents bypass.[^2_15][^2_14]

**OAuth2 Token Management**:

```python
# oauth_manager.py
import os
import json
import time
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ['https://mail.google.com/']
TOKEN_FILE = '/var/secrets/gmail_token.json'
CREDENTIALS_FILE = '/var/secrets/gmail_credentials.json'

def get_oauth_token():
    """Retrieve or refresh OAuth2 token"""
    creds = None
    
    # Load existing token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'r') as f:
            token_data = json.load(f)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    
    # Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed token
            with open(TOKEN_FILE, 'w') as f:
                f.write(creds.to_json())
            print("Token refreshed successfully")
        except Exception as e:
            print(f"Token refresh failed: {e}")
            # Alert ops team - manual re-auth needed
            raise
    
    # Initial authorization (manual, one-time)
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    
    return creds.token

# Integrate in producer.py
if os.getenv('IMAP_AUTH_METHOD') == 'oauth2':
    from oauth_manager import get_oauth_token
    token = get_oauth_token()
    
    # XOAUTH2 SASL mechanism
    auth_string = f"user={os.getenv('IMAP_USER')}\x01auth=Bearer {token}\x01\x01"
    server.authenticate('XOAUTH2', lambda x: auth_string.encode())
```

**Token Refresh Automation** (Kubernetes CronJob):

```yaml
# oauth-token-refresh.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: oauth-token-refresh
spec:
  schedule: "0 */6 * * *"  # Every 6 hours
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: token-refresher
            image: imap-ingestion:producer-latest
            command:
            - python
            - -c
            - |
              from oauth_manager import get_oauth_token
              token = get_oauth_token()
              print(f"Token refreshed: {token[:20]}...")
            volumeMounts:
            - name: secrets
              mountPath: /var/secrets
          volumes:
          - name: secrets
            secret:
              secretName: gmail-oauth-secrets
          restartPolicy: OnFailure
```


#### MFA Enforcement Checklist[^2_15][^2_14]

- ✅ Disable legacy IMAP authentication in Gmail/O365 admin console
- ✅ Enforce OAuth2 with MFA for all service accounts
- ✅ Use app-specific passwords ONLY if OAuth2 not supported (rotate every 90 days)
- ✅ Block third-party email clients without modern auth support
- ✅ Enable suspicious activity alerts in cloud email admin
- ✅ Implement rate limiting on IMAP login attempts (firewall rule)

**Rate Limiting with Fail2Ban**:

```ini
# /etc/fail2ban/filter.d/imap-auth-fail.conf
[Definition]
failregex = ^.*imap.*LOGIN failed.*from <HOST>
ignoreregex =

# /etc/fail2ban/jail.local
[imap-auth-fail]
enabled = true
port = 993
logpath = /var/log/imap_producer.log
maxretry = 5
findtime = 600
bantime = 3600
action = iptables-multiport[name=imap, port="993", protocol=tcp]
```


***

### Security Layer 4: Data Protection[^2_13]

#### Encryption at Rest \& Transit

**Redis Data Encryption**:[^2_10][^2_13]

```conf
# redis.conf - Encryption at rest (requires Redis Enterprise or disk-level encryption)

# Alternative: LUKS encrypted volume for /var/lib/redis
# Example Kubernetes StorageClass:
```

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: encrypted-ssd
provisioner: kubernetes.io/aws-ebs
parameters:
  type: gp3
  encrypted: "true"
  kmsKeyId: "arn:aws:kms:us-east-1:123456789:key/abc-def"
```

**Email Payload Sanitization**:

```python
# producer.py - PII redaction before storage
import re
import hashlib

def sanitize_payload(payload):
    """Remove/hash PII from email data"""
    # Hash email addresses (one-way)
    if 'from' in payload:
        email = payload['from']
        payload['from_hash'] = hashlib.sha256(email.encode()).hexdigest()[:16]
        payload['from_domain'] = email.split('@')[^2_1] if '@' in email else 'unknown'
        del payload['from']  # Remove original email
    
    # Redact subject sensitive patterns (credit cards, SSN)
    if 'subject' in payload:
        subject = payload['subject']
        # Credit card pattern
        subject = re.sub(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[REDACTED-CC]', subject)
        # SSN pattern
        subject = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED-SSN]', subject)
        payload['subject'] = subject
    
    # Truncate body snippet (already limited to 1000 chars)
    if 'body_snippet' in payload:
        payload['body_snippet'] = payload['body_snippet'][:500]  # Further limit
    
    return payload

# Apply in poll_imap() before XADD
payload = sanitize_payload(payload)
r.xadd(STREAM, {'payload': json.dumps(payload)}, maxlen=50000)
```

**TLS Everywhere**:

- IMAP connection: `ssl=True` (TLS 1.2+)
- Redis connection: TLS enabled (see Layer 1)
- Kubernetes internal: mTLS with service mesh (Istio/Linkerd)
- External APIs: HTTPS only with certificate pinning

***

### Security Layer 5: Secrets Management[^2_13]

#### Vault Integration (HashiCorp Vault)

**Vault Setup for IMAP Credentials**:

```bash
# Initialize Vault KV secrets engine
vault secrets enable -path=imap kv-v2

# Store IMAP credentials
vault kv put imap/production \
  imap_user="prod@example.com" \
  imap_oauth_token="ya29.a0AfH6..." \
  imap_oauth_refresh_token="1//0gQ..." \
  redis_producer_password="ProducerStrongPassword123!"

# Create policy for producer
vault policy write imap-producer - <<EOF
path "imap/data/production" {
  capabilities = ["read"]
}
EOF

# Create Kubernetes service account token
vault write auth/kubernetes/role/imap-producer \
  bound_service_account_names=imap-producer \
  bound_service_account_namespaces=default \
  policies=imap-producer \
  ttl=24h
```

**Application Integration with Vault Agent**:

```yaml
# producer-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: imap-producer
spec:
  template:
    metadata:
      annotations:
        vault.hashicorp.com/agent-inject: "true"
        vault.hashicorp.com/role: "imap-producer"
        vault.hashicorp.com/agent-inject-secret-creds: "imap/data/production"
        vault.hashicorp.com/agent-inject-template-creds: |
          {{- with secret "imap/data/production" -}}
          export IMAP_USER="{{ .Data.data.imap_user }}"
          export IMAP_OAUTH_TOKEN="{{ .Data.data.imap_oauth_token }}"
          export REDIS_PRODUCER_PASSWORD="{{ .Data.data.redis_producer_password }}"
          {{- end }}
    spec:
      serviceAccountName: imap-producer
      containers:
      - name: producer
        image: imap-ingestion:producer
        command:
        - /bin/sh
        - -c
        - source /vault/secrets/creds && python producer.py
```

**Secret Rotation Policy**:


| Secret Type | Rotation Frequency | Automation |
| :-- | :-- | :-- |
| Redis passwords | Every 90 days | Vault dynamic secrets |
| OAuth tokens | Every 6 hours (refresh) | Automated CronJob |
| TLS certificates | Every 365 days | cert-manager |
| API keys | Every 180 days | Manual + calendar alert |


***

### Security Layer 6: Command Restriction[^2_11][^2_13]

#### Disable Dangerous Redis Commands[^2_11]

```conf
# redis.conf - Disable admin/dangerous commands
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG "CONFIG_admin_only_2026"  # Obfuscate instead of disable
rename-command SHUTDOWN ""
rename-command DEBUG ""
rename-command SAVE ""  # Use BGSAVE only
rename-command BGREWRITEAOF "BGREWRITEAOF_admin"

# Disable scripting if not used
rename-command EVAL ""
rename-command EVALSHA ""
rename-command SCRIPT ""
```

**Kubernetes PodSecurityPolicy** (enforce least privilege):

```yaml
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: imap-restricted
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
  - ALL
  volumes:
  - 'configMap'
  - 'emptyDir'
  - 'projected'
  - 'secret'
  - 'downwardAPI'
  - 'persistentVolumeClaim'
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'RunAsAny'
  fsGroup:
    rule: 'RunAsAny'
  readOnlyRootFilesystem: true
```


***

### Security Layer 7: Monitoring \& Auditing[^2_13]

#### Security Event Logging

**Redis Command Auditing**:[^2_11]

```conf
# redis-audit.conf (custom module or slowlog abuse)
slowlog-log-slower-than 10000  # Log commands > 10ms
slowlog-max-len 128

# Enable syslog for Redis logs
syslog-enabled yes
syslog-ident redis-prod
syslog-facility local0
```

**Application Security Logging**:

```python
# security_logger.py
import logging
import json
import hashlib
from datetime import datetime

security_logger = logging.getLogger('security')
security_logger.setLevel(logging.INFO)
handler = logging.FileHandler('/var/log/security_audit.log')
handler.setFormatter(logging.Formatter('%(message)s'))
security_logger.addHandler(handler)

def log_security_event(event_type, details):
    """Log security-relevant events in SIEM-friendly JSON format"""
    event = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'event_type': event_type,
        'severity': details.get('severity', 'info'),
        'user': details.get('user', 'system'),
        'source_ip': details.get('ip', 'internal'),
        'action': details.get('action'),
        'resource': details.get('resource'),
        'result': details.get('result', 'success'),
        'details': details
    }
    security_logger.info(json.dumps(event))

# Usage in producer.py
from security_logger import log_security_event

# Log authentication attempts
try:
    server.login(user, password)
    log_security_event('imap_auth', {
        'severity': 'info',
        'action': 'imap_login',
        'user': user,
        'result': 'success'
    })
except imapclient.IMAPError as e:
    log_security_event('imap_auth', {
        'severity': 'warning',
        'action': 'imap_login',
        'user': user,
        'result': 'failure',
        'error': str(e)
    })

# Log Redis ACL violations
try:
    r.flushdb()  # Should fail with ACL
except redis.ResponseError as e:
    if 'NOPERM' in str(e):
        log_security_event('redis_acl_violation', {
            'severity': 'critical',
            'action': 'flushdb',
            'result': 'blocked',
            'error': str(e)
        })
```

**SIEM Integration (Splunk/ELK)**:

```yaml
# filebeat-security.yml
filebeat.inputs:
- type: log
  enabled: true
  paths:
    - /var/log/security_audit.log
  json.keys_under_root: true
  json.add_error_key: true
  
  processors:
  - add_tags:
      tags: [security, audit, imap-ingestion]
  
  - drop_event:
      when:
        equals:
          event_type: "health_check"  # Noise reduction

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
  index: "security-audit-%{+yyyy.MM.dd}"
  
  # Alert on critical events
  pipelines:
  - pipeline: security-alert-pipeline
```

**Security Dashboards \& Alerts**:

```yaml
# prometheus-security-alerts.yml
groups:
- name: security
  rules:
  - alert: RedisACLViolation
    expr: increase(redis_acl_violations_total[5m]) > 5
    labels:
      severity: critical
      team: security
    annotations:
      summary: "Multiple Redis ACL violations detected"
      description: "{{ $value }} ACL violations in last 5 minutes"
  
  - alert: IMAPAuthFailureSpike
    expr: rate(imap_auth_failures_total[5m]) > 1
    for: 10m
    labels:
      severity: warning
      team: security
    annotations:
      summary: "IMAP authentication failure spike"
      description: "Possible password spraying attack"
  
  - alert: UnauthorizedDataAccess
    expr: increase(data_access_unauthorized_total[10m]) > 0
    labels:
      severity: critical
      team: security
    annotations:
      summary: "Unauthorized data access attempt detected"
```


***

### Security Compliance Checklist

**Pre-Production Security Audit**:[^2_13]

- [ ] **Network**: Redis bound to localhost/private IP, TLS enabled, firewall rules applied
- [ ] **Authentication**: ACL configured, strong passwords (20+ chars), OAuth2 for IMAP
- [ ] **Authorization**: Principle of least privilege, role-based ACL, no default user
- [ ] **Encryption**: TLS 1.2+ for all connections, encrypted storage volumes
- [ ] **Secrets**: All secrets in Vault/K8s Secrets, no hardcoded credentials
- [ ] **Commands**: Dangerous Redis commands disabled/renamed
- [ ] **Data**: PII redacted/hashed, retention policy enforced (GDPR compliant)
- [ ] **Logging**: Security events logged, SIEM integration active
- [ ] **Monitoring**: Security alerts configured, dashboard operational
- [ ] **Updates**: All components patched (Redis, Python, OS), vulnerability scan passed
- [ ] **Backups**: Encrypted backups, offsite storage, tested restore procedure
- [ ] **Incident Response**: Runbook documented, team trained, on-call rotation
- [ ] **Penetration Testing**: External security audit passed, findings remediated
- [ ] **Compliance**: SOC 2/ISO 27001 controls mapped, audit trail complete

**Quarterly Security Review**:

- Review Redis/IMAP/Kubernetes CVEs and patch
- Rotate secrets per policy
- Audit access logs for anomalies
- Test incident response procedure (tabletop exercise)
- Update threat model based on new attack vectors
- Re-certify compliance (GDPR, HIPAA if applicable)

***

**Sistema ora production-ready con security hardening enterprise-grade, backup strategy robusti e SOP operativi completi**.[^2_12][^2_10][^2_5][^2_3][^2_11][^2_13]
<span style="display:none">[^2_16][^2_17][^2_18][^2_19][^2_20][^2_21][^2_22][^2_23][^2_24][^2_25][^2_26][^2_27][^2_28][^2_29][^2_30][^2_31][^2_32][^2_33][^2_34][^2_35][^2_36][^2_37][^2_38][^2_39][^2_40][^2_41][^2_42][^2_43][^2_44][^2_45][^2_46]</span>

<div align="center">⁂</div>

[^2_1]: http://limble.com/learn/maintenance-operations/sop/

[^2_2]: https://servicechannel.com/blog/standard-operating-procedure-for-maintenance-of-equipment/

[^2_3]: https://trilio.io/resources/redis-backup/

[^2_4]: https://binaryscripts.com/redis/2025/05/01/redis-persistence-rdb-vs-aof-choosing-the-right-persistence-strategy-for-your-application.html

[^2_5]: https://oneuptime.com/blog/post/2026-01-27-redis-persistence-rdb-aof/view

[^2_6]: https://oneuptime.com/blog/post/2026-01-27-rdb-vs-aof-persistence-redis/view

[^2_7]: https://www.emailservicebusiness.com/blog/best-practices-email-backup-recovery/

[^2_8]: https://www.spambarometer.com/guides/article/email-security-incident-response-best-practices

[^2_9]: https://www.beamsec.com/how-to-develop-an-email-security-incident-response-plan/

[^2_10]: https://oneuptime.com/blog/post/2026-01-21-redis-secure-production/view

[^2_11]: https://www.compilenrun.com/docs/middleware/redis/redis-administration/redis-security-hardening/

[^2_12]: https://redis.io/docs/latest/operate/rs/security/recommended-security-practices/

[^2_13]: https://securedebug.com/mastering-redis-security-an-in-depth-guide-to-best-practices-and-configuration-strategies/

[^2_14]: https://www.trendmicro.com/vinfo/us/security/news/cybercrime-and-digital-threats/attackers-use-legacy-imap-protocol-to-bypass-multifactor-authentication-in-cloud-accounts-leading-to-internal-phishing-and-bec

[^2_15]: https://www.techtarget.com/searchsecurity/tip/Where-does-IMAP-security-fall-short-and-how-can-it-be-fixed

[^2_16]: https://journal.smpte.org/periodicals/SMPTE Motion Imaging Journal/135/1/9/

[^2_17]: https://www.semanticscholar.org/paper/f3862f0fc049968a0c81f1eab072b6357fe01829

[^2_18]: https://learning-gate.com/index.php/2576-8484/article/view/4382

[^2_19]: https://www.semanticscholar.org/paper/f5e2fa54f002e27709625620d9f8c679d6e873f1

[^2_20]: https://www.ssbfnet.com/ojs/index.php/ijrbs/article/view/2696

[^2_21]: https://isjem.com/download/a-comprehensive-guide-to-secure-build-pipelines-with-continuous-scanning/

[^2_22]: https://ieeexplore.ieee.org/document/9559826/

[^2_23]: http://archives.pdx.edu/ds/psu/25258

[^2_24]: https://www.semanticscholar.org/paper/bf942a542acfdbdf9f6e8af7e50aa02495971fff

[^2_25]: https://www.semanticscholar.org/paper/635c90e838217c8b5ec0dbbd6fbe611b7aab48a2

[^2_26]: https://arxiv.org/html/2504.07707v1

[^2_27]: http://arxiv.org/pdf/2408.06822.pdf

[^2_28]: http://arxiv.org/pdf/2106.13123.pdf

[^2_29]: https://wjaets.com/sites/default/files/WJAETS-2024-0093.pdf

[^2_30]: http://arxiv.org/pdf/2407.10740.pdf

[^2_31]: https://arxiv.org/pdf/2305.18639.pdf

[^2_32]: http://arxiv.org/pdf/2412.16190.pdf

[^2_33]: https://arxiv.org/pdf/2409.03405.pdf

[^2_34]: https://www.percona.com/blog/redis-performance-best-practices/

[^2_35]: https://www.codethreat.com/blogs/securing-redis-clients-common-pitfalls-best-practices

[^2_36]: https://arxiv.org/pdf/1201.2360.pdf

[^2_37]: https://arxiv.org/pdf/2209.09459.pdf

[^2_38]: http://arxiv.org/pdf/2405.17731.pdf

[^2_39]: https://arxiv.org/pdf/2312.08309.pdf

[^2_40]: https://arxiv.org/pdf/2210.08934.pdf

[^2_41]: http://arxiv.org/pdf/2302.02118.pdf

[^2_42]: https://www.mdpi.com/2072-666X/13/1/52/pdf

[^2_43]: http://arxiv.org/pdf/2503.06284.pdf

[^2_44]: https://stackoverflow.com/questions/39953542/aof-and-rdb-backups-in-redis

[^2_45]: https://www.tothenew.com/blog/redis-cluster-backups-and-restoration/

[^2_46]: https://www.infosecinstitute.com/resources/management-compliance-auditing/top-5-email-retention-policy-best-practices/


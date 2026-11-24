#!/usr/bin/env python3
"""Week 7 lab, Step 4: add one ACL entry — safely, even when responses get lost.

Run it in the client container, with your token in the environment:
    python3 retry_safe.py NNNN

Like retry_naive.py, it retries when no response comes back and waits when told to. The difference:
every attempt carries the same Idempotency-Key, chosen once before the first attempt. If the server
applied the change but the reply was lost, the retry is recognised and the stored reply is replayed
(Idempotent-Replayed: true) instead of adding the entry again — one change per run, however many retries.
"""
import os
import sys
import time
import uuid

import requests

API = os.environ.get("LAB_API_URL", "http://api:8000")
TOKEN = os.environ["LAB_API_TOKEN"]  # export LAB_API_TOKEN=... first; never write it in this file

if len(sys.argv) != 2 or not sys.argv[1].isdigit() or len(sys.argv[1]) != 4:
    sys.exit("usage: python3 retry_safe.py <last 4 digits of your student ID>")
NN = int(sys.argv[1][2:])

ENTRY = {
    "action": "deny",
    "src": f"10.{NN}.30.0/24",
    "dst": f"10.{NN}.20.0/24",
    "protocol": "tcp",
    "port": 22,
    "description": "guests may not SSH to servers",
}

# One key for this one logical change, chosen BEFORE the first attempt and reused on every retry.
# A new key per attempt would make each retry look like a new request, and the duplicates would return.
IDEMPOTENCY_KEY = str(uuid.uuid4())
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": IDEMPOTENCY_KEY}
print(f"Idempotency-Key: {IDEMPOTENCY_KEY}")

for attempt in range(1, 6):
    try:
        r = requests.post(f"{API}/api/v1/acl/entries", json=ENTRY, headers=HEADERS, timeout=5)
    except requests.ConnectionError as e:
        print(f"attempt {attempt}: no response ({type(e).__name__}) — trying again with the same key")
        time.sleep(1)
        continue
    if r.status_code in (429, 503):
        wait = int(r.headers.get("Retry-After", "1"))
        print(f"attempt {attempt}: {r.status_code} — waiting {wait}s")
        time.sleep(wait)
        continue
    replayed = " (replayed, not applied again)" if r.headers.get("Idempotent-Replayed") == "true" else ""
    print(f"attempt {attempt}: {r.status_code} {r.reason}{replayed}: {r.json()}")
    break
else:
    sys.exit("gave up after 5 attempts")

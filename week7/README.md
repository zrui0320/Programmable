# Week 7 Lab: Talking to a network API: HTTP, status codes, idempotency, paging and tokens

My addresses: `NN = 40`, `NNNN = 3540`, so IPv4 `10.40.0.0/16` and IPv6 `fd00:3540::/32`. The API (`labapi.py --id 3540`) gave me **240 devices** (200 + 40).

Everything below is real output from my machine. The API and my client each ran in a `python:3.12` container on a Docker network called `apilab`. No token or password is stored in any file: the scripts in `lab7/` were run with `bash -v`, which prints each line as written, so they only ever show `$TOKEN`.

| File | What it shows |
|---|---|
| [lab7/network-inspect.txt](lab7/network-inspect.txt) | `docker network inspect apilab`: bridge driver, subnet, both containers |
| [lab7/bridge-veth.txt](lab7/bridge-veth.txt) | The Linux bridge and the two veth ends on the laptop |
| [lab7/step2.sh](lab7/step2.sh) → [lab7/status.txt](lab7/status.txt) | Step 2: 401, 404, 405, 400, 422, 409, 403 and the 429s, full responses |
| [lab7/step3.sh](lab7/step3.sh) → [lab7/step3.txt](lab7/step3.txt) | Step 3: each change sent twice, status codes |
| [lab7/audit-step3.txt](lab7/audit-step3.txt) | Step 3: what the server actually applied |
| [lab7/audit-step4-naive.txt](lab7/audit-step4-naive.txt) | Step 4: `retry_naive.py` with half the responses lost: 8 entries |
| [lab7/audit-step4.txt](lab7/audit-step4.txt) | Step 4: `retry_safe.py`, 3 runs, 3 entries |
| [lab7/retry-safe-runs.txt](lab7/retry-safe-runs.txt) | Output of one further `retry_safe.py` run (it needed no retry) |
| [lab7/inventory.txt](lab7/inventory.txt) | Step 5: `inventory.py` against 503s and 429s: 240/240 in 30 requests |
| [lab7/expiry.txt](lab7/expiry.txt) | Step 6: 5-second tokens, 2 requests/s: two re-logins, 240/240 in 37 requests |
| [lab7/openapi.json](lab7/openapi.json) | Step 7: the API's own OpenAPI 3.1 document |
| [lab7/step7-answers.sh](lab7/step7-answers.sh) → [lab7/step7-answers.txt](lab7/step7-answers.txt) | Step 7: the four answers read from the document |
| [lab7/step7-site.sh](lab7/step7-site.sh) → [lab7/site-penang.txt](lab7/site-penang.txt) | Step 7: the 60 devices at `penang`, using `?site=` |

My code: [retry_safe.py](retry_safe.py) (Step 4) and `get_page()` / `get_all()` in [inventory.py](inventory.py) (Steps 5 and 6).

**Step 1:** without a token, `GET /api/v1/vlans` gave `401 Unauthorized` with `WWW-Authenticate: Bearer realm="labapi"`. With the token it gave `200 OK`. The header that says what format the body is in is **`Content-Type: application/json`**.

## 1. What `docker network inspect apilab` shows, and how it's built

The inspect output shows a network with **driver `bridge`**, subnet **`172.19.0.0/16`** and gateway **`172.19.0.1`**, and two containers on it:

| Container | IPv4 | MAC |
|---|---|---|
| `api` | 172.19.0.2/16 | 66:98:82:2d:b1:df |
| `client` | 172.19.0.3/16 | 76:be:3e:26:4c:a0 |

These are exactly the Week 6 building blocks, put together by Docker:

- **Each container is a set of namespaces**, including its own **network namespace**. So `api` and `client` each have their own `eth0`, address and routing table.
- **Each `eth0` is one end of a veth pair.** The other ends are on the laptop: `veth518d1d3` (host index 6) and `vethbf878f4` (host index 7). Inside the containers, `/sys/class/net/eth0/iflink` was 6 for `api` and 7 for `client`, which matches each container to its veth on the host, just like `h1-eth0@if5` in Week 6.
- **The cable between them is a Linux bridge** called `br-1d9d5dee951b`. Its name is the network ID `1d9d5dee951b…` from the inspect output. `bridge link` shows both veths with `master br-1d9d5dee951b state forwarding`, which is the same as `s1` and `br0` in Week 6.
- **The gateway `172.19.0.1` is the bridge's own address on the laptop.** Docker adds NAT rules so the containers can reach the internet through it.
- **Docker adds one thing Week 6 didn't have: a DNS server.** On a user-made network, each container's `/etc/resolv.conf` points at `127.0.0.11`, Docker's built-in DNS. That's how `client` reaches the API by the name `api` instead of `172.19.0.2`.

## 2. What a client should do after each Step 2 status code

All of these are in [lab7/status.txt](lab7/status.txt), with the server's own message:

| Code | How I got it | Server said | What the client should do next | Retry? |
|---|---|---|---|---|
| **401** Unauthorized | `GET /vlans` with no token | "authentication required…" + `WWW-Authenticate: Bearer` | Log in (again, if the token expired) and send the same request with the new token | **Yes, once**, after logging in |
| **404** Not Found | `GET /vlans/77` | "VLAN 77 not found" | Fix the URL or ID. For a DELETE, treat it as "already gone" | **Never** |
| **405** Method Not Allowed | `POST /vlans/10` | "POST is not allowed on a single VLAN" + `Allow: GET, PUT, PATCH, DELETE` | Use a method from the `Allow` header, e.g. PUT to create or replace | **Never** |
| **400** Bad Request | `POST /vlans` with `{"id": 30, "name": guest}` | "the request body is not valid JSON" | Fix the request: send valid JSON | **Never** |
| **422** Unprocessable Entity | `PUT /vlans/5000` | "id must be a VLAN number from 1 to 4094" | The JSON is valid but the value isn't: fix the data | **Never** |
| **409** Conflict | `POST /vlans` with VLAN 10 | "VLAN 10 already exists: use PUT to replace it" | Read the current state (GET), then decide: leave it, or PUT to replace it | **Never** as-is |
| **403** Forbidden | `PUT /vlans/30` with the viewer's token | "viewer has a read-only token" | Stop: this user isn't allowed. Logging in again as the same user won't help | **Never** |
| **429** Too Many Requests | 10 GETs in a row | "too many requests: at most 5 a second" + `Retry-After: 1` | Wait the `Retry-After` seconds, then send the same request again | **Yes**, after waiting |

The ten GETs gave `200 200 200 200 200 429 429 429 429 429`: the first five fit in the 5-per-second limit and the rest were refused. Step 5 also gave **503** Service Unavailable, which should be retried with exponential backoff (1, 2, 4, 8 s… plus some randomness).

**Never retry 400, 403, 404, 405, 409 or 422 unchanged.** They are the client's own mistakes, and sending the same request again gives the same answer. Only 429 and 503 (temporary, on the server's side) and 401 (after a fresh login) are worth retrying.

## 3. Why `retry_naive.py` created duplicates, and how my fix stops them

**Step 3 showed which requests are safe to repeat** ([lab7/step3.txt](lab7/step3.txt), [lab7/audit-step3.txt](lab7/audit-step3.txt)):

| Sent twice | 1st | 2nd | Changes the server applied |
|---|---|---|---|
| `PUT /vlans/30 {"name": "guests"}` | 201 | 200 | 1: created VLAN 30 |
| `DELETE /vlans/99` | 204 | 404 | 1: deleted VLAN 99 |
| `POST /acl/entries` | 201 | 201 | **2: entries 1 and 2, identical** |
| `POST /acl/entries` + `Idempotency-Key: guests-no-ssh-1` | 201, `Location: …/3` | 201, `Location: …/3`, `Idempotent-Replayed: true` | 1: entry 3 |

PUT and DELETE describe the end state ("VLAN 30 is called guests", "VLAN 99 is gone"), so doing them twice leaves the server the same as doing them once. The status codes differ, but the state doesn't. POST means "add one more", so every repeat adds another entry.

**Why the naive script made duplicates.** With `lose_responses: 0.5`, the API *applied* the POST and then dropped the connection before replying. To the client this looks exactly like a timeout: it got `ConnectionError`, assumed nothing had happened, and sent the same POST again. Each retry was a new "add one more". In [lab7/audit-step4-naive.txt](lab7/audit-step4-naive.txt), three runs added **8 identical entries**. Judging by the timestamps and the API log, the last run lost all five replies and gave up with "gave up after 5 attempts", yet it had added **5** entries (08:35:06–08:35:10). The script reported failure after changing the network five times.

**How my fix prevents it.** [retry_safe.py](retry_safe.py) makes one change:
```python
IDEMPOTENCY_KEY = str(uuid.uuid4())   # once, before the first attempt
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": IDEMPOTENCY_KEY}
```
Every attempt in the retry loop sends the **same** key. The server stores *key → (fingerprint of the request, status, body)* the first time it applies a change. When the same key arrives again with the same body, it returns the stored response with `Idempotent-Replayed: true` and doesn't apply anything. In [lab7/audit-step4.txt](lab7/audit-step4.txt), three runs with the chaos still on gave **exactly three entries (12, 13, 14), one per run**. A retry really happened: entry 13 was applied at **08:39:03**, but the reply the client got was logged at **08:39:04**. The first reply was lost, the client waited 1 s and retried with the same key, and the server replayed the result instead of adding a second entry.

**Why the key must be chosen once, before the first attempt.** The key identifies *the change*, not *the attempt*. If the script made a new key inside the loop, each retry would carry a key the server had never seen. The server would treat it as a new change and add another entry, and the duplicates would come back. The key also has to exist *before* attempt 1, because attempt 1 is the one whose reply may be lost: the server must have stored it under the key that the retry will send. A new run of the script is a new change, so it gets a new key, which is why three runs correctly give three entries.

## 4. How many requests `inventory.py` sent, and why more than the number of pages

`main()` reads `/api/v1/devices?limit=10`. With 240 devices that's **24 pages**, so 24 requests if nothing goes wrong. `self.requests` counts every GET `get_page()` sends, retries included. Logins are separate POSTs and aren't counted.

| Run | Conditions | Requests | Breakdown |
|---|---|---|---|
| Step 5 ([inventory.txt](lab7/inventory.txt)) | 20% 503s, 5 requests/s | **30** | 24 pages + 3 retries after 503 + 3 retries after 429 (+1 login) |
| Step 6 ([expiry.txt](lab7/expiry.txt)) | 5-second tokens, 2 requests/s | **37** | 24 pages + 11 retries after 429 + 2 resends after 401 (+3 logins) |

Both runs read **240 devices, matching `X-Total-Count: 240`**, with 60 at each of `johor`, `kl-hq`, `penang` and `pj-dc`. Every `mgmt_ip` in `devices.json` is in my `10.40.0.0/16` (e.g. `sw-johor-001` at `10.40.203.1`).

**Why more requests than pages:** a request that gets a 503, 429 or 401 returns no devices, but it was still sent, and the page has to be requested again.
- **503:** the server was temporarily unavailable. `get_page()` waits 1, 2, 4, 8… seconds plus some randomness, and gives up after 6 failures in a row.
- **429:** the client asked faster than the rate limit. `get_page()` waits the `Retry-After` seconds the server sends.
- **401:** in Step 6 the read took about 15 s with 5-second tokens, so the token expired twice part-way through. `get_page()` logged in again and resent the same page, so no page was skipped.

`get_all()` follows the `next` URL in each response body until it is `null`, and takes `X-Total-Count` from the first response. Because it uses the server's `next` URL rather than building page URLs itself, it can't skip or repeat a page.

## 5. What the OpenAPI document told me that I'd never have guessed

The API describes itself at `/openapi.json` ([lab7/openapi.json](lab7/openapi.json)). From the document alone ([lab7/step7-answers.txt](lab7/step7-answers.txt)):

- **Largest `limit` on `/api/v1/devices`:** **50** (minimum 1, default 20).
- **Methods on `/api/v1/acl/entries/{id}`:** only **GET and DELETE**. An ACL entry can't be edited: to change one you delete it and add a new one.
- **`Idempotency-Key` reused with a different body:** **rejected with 422**. The same key with the same body replays the first result.
- **Only one site's devices:** a **`site`** query parameter (`kl-hq`, `pj-dc`, `penang` or `johor`), which the lecture never mentioned. `GET /devices?site=penang&limit=50` returned `X-Total-Count: 60`, which counts only penang. Following `next` gave the last 10, ending at `sw-penang-060` (`10.40.202.60`) with `next: null`, so 60 devices in all ([lab7/site-penang.txt](lab7/site-penang.txt)). The `next` URL kept `site=penang` by itself.

What I'd never have guessed:
- **Paging uses an opaque cursor, not page numbers.** The `next` URL contains `cursor=c3ctcGVuYW5nLTA1MA`, which is just base64 of `sw-penang-050`, the last device on the previous page. The document calls it "opaque: take it from the previous page's next URL", so clients must follow `next` instead of building cursors themselves. A made-up cursor gets a 400.
- **VLANs accept PATCH**, to change only some fields.
- **The document isn't the whole API.** The `/api/v1/lab/chaos` and `/api/v1/lab/audit` endpoints I used all week aren't in it at all. The six documented paths are `auth/token`, `vlans`, `vlans/{id}`, `acl/entries`, `acl/entries/{id}` and `devices`.

---

# Assignment: Make the API match my intent

[intent.yaml](intent.yaml) is the whole truth about what the API should hold, and [sync.py](sync.py) makes the API match it:

```
export LAB_USERNAME=netops LAB_PASSWORD=...     # in the container shell, never in a file
python3 sync.py intent.yaml --dry-run           # read the API, print the plan, change nothing
python3 sync.py intent.yaml                     # plan, apply, then read back and check
```

**My intent (VLAN *n* = `10.40.n.0/24`):**
- **6 VLANs:** 10 staff, 20 servers, 30 guests, 40 voice, 50 mgmt, 60 iot.
- **5 ACL entries, in order:**
  1. permit tcp/443 staff → servers
  2. deny tcp/22 guests → servers
  3. permit udp/5060 voice → servers
  4. deny any iot → mgmt
  5. permit icmp mgmt → `10.40.0.0/16`

A fresh API holds VLANs 1 default, 10 users, 20 servers and 99 old-guest, and no ACL. Against that, the intent covers every kind of change: a rename (10), creates (30–60), a delete (99), no change (20) and five ACL adds.

| Evidence (API restarted fresh, except for the second run) | What it shows |
|---|---|
| [evidence/dry-run.txt](evidence/dry-run.txt) | `Plan: 11 changes`, `Dry run: nothing changed`, then the audit: **`"writes": 0`** |
| [evidence/apply.txt](evidence/apply.txt) | Chaos `{"lose_responses": 0.3, "errors_503": 0.2}`: 3 × 503, 3 lost replies (all recovered), `RESULT: MATCHES`. The audit shows **exactly 11 writes**, one per plan step, and `GET /vlans` and `GET /acl/entries` are exactly the intent, with no duplicates |
| [evidence/second-run.txt](evidence/second-run.txt) | Same API, chaos still on: `Plan: nothing to do`, `RESULT: MATCHES - no write sent`. The audit has **11 writes before and 11 after** |
| [evidence/expiry.txt](evidence/expiry.txt) | `--token-ttl 3 --rate 2`: **2 × `401: token expired, logging in again`** (3 logins in all) and 7 × 429, then `RESULT: MATCHES` with 11 writes |

## E4.1 How `sync.py` decides what to change

`sync.py` never just "sends the file". It works in four stages:

1. **Check the intent before sending anything.**
   - `load_intent()` rejects a bad file and lists every problem: VLAN IDs outside 2–4094 or listed twice, bad names, bad CIDRs or protocols, or a port on anything except tcp/udp.
   - This way a typo can't stop the sync half-way, after some changes are already made.
2. **Read the current state:** `GET /api/v1/vlans` and `GET /api/v1/acl/entries`, following `next` links.
3. **Plan the difference** (`make_plan()`):
   - **VLANs** are compared as `{id: name}`:
     - in the intent but not on the API → `PUT` (create);
     - on both with a different name → `PUT` (rename);
     - on the API but not in the intent, and not VLAN 1 → `DELETE`;
     - the same on both → nothing.
   - **ACL entries** are first **normalised** into the exact shape the API returns: `protocol` defaults to `"any"`, `port` to `null`, `description` to `""`, and the server's `id` is dropped. Without this, entry 4 (no port) would never compare equal, and every run would want to change it.

     Order matters in an ACL, so the comparison is on ordered lists:
     - keep the longest **prefix** that already matches the intent;
     - `DELETE` every entry on the API after that prefix;
     - `POST` the rest of the intent in order.
4. **Apply the plan, then check it.** Read the state back and **plan again**: an empty plan means the API matches, so it prints `RESULT: MATCHES`. If anything is left, it prints `DOES NOT MATCH` with the remaining differences and exits 1.

**Walking through my apply run** ([evidence/apply.txt](evidence/apply.txt)):
- **VLAN 30 was created.** The API had VLANs 1, 10, 20 and 99, so 30 was missing, and the plan said `PUT /api/v1/vlans/30  create VLAN 30 guests`.
  - The first two attempts got **503** (`try 1 failed`, `try 2 failed`). The API returns 503 before changing anything, so `sync.py` backed off 1.2 s, then 2.4 s, and the third attempt returned **201**.
  - The audit has exactly one `created VLAN 30 guests` (seq 2).
- **VLAN 10 was renamed.** The API had `users` and the intent says `staff`, so the plan said `rename VLAN 10 users -> staff`. One 503, then **200**, and the audit has `set VLAN 10 staff` (seq 1).
- **VLAN 99 was deleted.** It's on the API but not in the intent (and isn't VLAN 1), so the plan said `DELETE /api/v1/vlans/99`.
  - The server deleted it and then **lost the reply**: `no response (ConnectionError): try 1 failed`.
  - DELETE is idempotent, so `sync.py` sent it again and got **404**. `send()` treats 404 on a DELETE as success, and the script printed `404 (already gone: an earlier attempt had worked)`.
  - The audit has a single `deleted VLAN 99` (seq 6).
- **VLAN 20** is `servers` on both sides, so there's no step for it.
- **The `voice-to-sip-server` ACL entry.** The API had no entries, so the matching prefix was 0 and all five intent entries were planned as POSTs, in order. This one got the key `sync-3fddc84a-…` in the plan.
  - The server added it as entry 3, but the reply was lost.
  - `sync.py` retried with **the same key and the same bytes**, and the server answered `201 (replayed: the first attempt had worked, not applied again)`.
  - The audit has **one** `added ACL entry 3: permit 10.40.40.0/24 → 10.40.20.0/24 udp/5060` (seq 9). The same happened to entry 5.
- **If someone changed the ACL by hand, the prefix rule would handle it.** For example, if entry 3 were deleted, the API would hold entries 1, 2, 4 and 5. Only entries 1–2 would still match the intent in order, so the plan would delete 4 and 5 and add 3, 4 and 5 again, restoring the intended order.

Because the plan is only the difference, **the second run sends nothing**. In [evidence/second-run.txt](evidence/second-run.txt), the API already held exactly the intent, the plan was empty, and the audit stayed at 11 writes even with chaos on.

## E4.2 Why each request is safe to retry, or why it isn't retried

Every request goes through one function, `Client.send()`. It encodes the body **once**, before its retry loop, so every retry sends identical bytes. It gives up after 6 failures in a row (503s or lost replies), backing off 1, 2, 4, 8, 16 s plus some randomness.

| Request | Why retrying it is safe | Evidence |
|---|---|---|
| `POST /api/v1/auth/token` (login) | Changes no configuration. A lost reply just leaves an unused token behind | 3 logins in `expiry.txt` |
| `GET /vlans`, `GET /acl/entries` | Read-only: asking twice changes nothing | Reads before and after applying |
| `PUT /api/v1/vlans/{id}` | **Idempotent.** It states the final result ("VLAN 30 is called guests"), so sending it again after a lost reply leaves the same state. The API only records a write when the name actually changes | VLAN 10 and 30 retried after 503s; one audit line each |
| `DELETE /api/v1/vlans/{id}` (and `/acl/entries/{id}`) | **Idempotent in effect.** A retry after a lost reply gets 404, which `send()` counts as success because the goal ("it's gone") is met | VLAN 99: lost reply, then `404 (already gone…)`, one `deleted VLAN 99` |
| `POST /api/v1/acl/entries` | **Not idempotent by itself**, since each POST adds one more entry. So `sync.py` **only ever sends it with an `Idempotency-Key`**. A retry with the same key and body is replayed by the server, not applied again | Entries 3 and 5: lost reply, then `201 (replayed…)`, 5 ACL adds in an 11-write audit |

**Which errors it retries, and how:**
- **503:** the lab API returns it *before* touching anything, so retrying any method is safe. `sync.py` backs off, and uses `Retry-After` if it's longer.
- **429:** also returned before any change. `sync.py` waits the `Retry-After` seconds.
- **401:** the token has expired, and the request was refused before any change. `sync.py` logs in again and resends. An `Idempotency-Key` isn't tied to the token, so a keyed POST that is resent after logging in is still recognised.
- **No response at all** (`ConnectionError` or `Timeout`): the change may or may not have happened, so `sync.py` retries **only** if the request is safe to repeat (`safe_to_repeat`): GET, PUT, DELETE, or a POST with a key.

**What it never retries:**
- **A lost reply to a POST without a key.** `send()` stops with "NOT safe to send again … check the API by hand", because a retry could create a duplicate. `sync.py` never sends such a POST, but the check is in the code so the rule can't be broken by accident.
- **400, 403, 404 (except on DELETE), 405, 409 and 422.** These are our own mistakes, and sending the same request again would get the same answer. `send()` raises `SyncError` with the server's message and the run stops.

## E4.3 PUT for VLANs, POST only for ACL entries: what changed in the design, and the keys

**VLANs are addressed by an ID the client chooses.** So `sync.py` can say what it wants directly, `PUT /vlans/30 {"name": "guests"}`, without caring whether VLAN 30 exists yet. Create and rename are the same operation, each VLAN is planned on its own, and retries are safe with no extra machinery. The VLAN ID is the natural key.

**ACL entries are different in three ways**, and each one shaped the design:
1. **The server chooses the ID** (`Location: /api/v1/acl/entries/3`). My intent can't refer to "entry 3", so entries are matched by their **content**, after normalising both sides into the same shape.
2. **There's no PUT or PATCH on an entry** (the OpenAPI document allows only GET and DELETE). An entry can't be edited, so a change means **delete and add again**.
3. **Order matters**, and POST always appends to the end. That's why the plan keeps the longest matching prefix, deletes everything after it, and runs the **deletes before the adds**.

Since POST is the only way to add an entry, and it isn't idempotent, every planned POST gets an **Idempotency-Key**.

**How I choose each key:** `make_plan()` creates `key = f"sync-{uuid.uuid4()}"` for each ACL entry it plans to add:
- **When:** when the plan is made, before the first attempt. One key per entry, per run.
- **Reused:** the key is kept in the plan step, and `send()` uses it on every retry of that step.
- **Same bytes:** the body is encoded once (`json.dumps(body, sort_keys=True)`), because the API's check is a SHA-256 of the raw body. A retry with different bytes would get a 422.
- **Visible:** the plan prints each key, so `apply.txt` shows which key each entry was sent with.

**A new key for every attempt** would break exactly the case the key exists for. When entry 3's reply was lost in `apply.txt`, the retry would have carried a key the server had never seen. The server would have treated it as a new request and added a **second** copy of `voice-to-sip-server`. The check would then find 6 entries instead of 5 and report `DOES NOT MATCH`. This is `retry_naive.py`'s bug from the lab.

**The same key for every run** (a fixed key, or one derived only from the entry's content) breaks the *next* run. The API remembers keys for as long as it runs.
- Suppose someone deletes `guests-no-ssh` by hand, and I run `sync.py` again. It plans to POST that entry with **the same key and the same body**. The server **replays the old `201`** pointing at the old, deleted entry, and adds nothing. `sync.py` would believe the entry was added, the check would find it missing, and every later run would fail the same way.
- If I changed the entry in `intent.yaml` but kept the key, the server would refuse it with **422** ("already used with a different request").

A key has to identify *one intended change*: the same across that change's retries, and new for the next run.

## E4.4 Two people running `sync.py` at the same moment

`sync.py` assumes nobody else is changing the API between its read and its writes. With two people, say A and B with different intents:
- **Both read the same state and make plans from it,** then their writes interleave. Each plan is already out of date while it runs.
- **VLANs:** each person **deletes the other's new VLANs** (they aren't in their own intent). Where both intents have the same VLAN with different names, the last PUT wins.
- **ACL:** each person's prefix rule deletes the entries the other has just appended, then appends their own. The result can be a **mix of both ACLs in a meaningless order**. With a deny and a permit from different intents interleaved, that's a real security problem.
- **Each check reads a state the other is still changing.** One or both may report `DOES NOT MATCH`. Worse, both may report `MATCHES` at different moments while the final state matches neither intent.
- **Running them again in turn just flips the network back and forth.** Idempotency keys don't help: they stop *my own* retries from duplicating, not two different clients from disagreeing.

**What the API would need to make this safe:**
- **Optimistic concurrency.** A version number or `ETag` on the VLAN and ACL collections, and writes that must send `If-Match: <version I read>`.
  - If someone else changed the state in between, the API answers **`412 Precondition Failed`** and changes nothing.
  - `sync.py` would then re-read, re-plan and try again, or stop and report the conflict.
- **Or a lock or lease:** take a lock on the configuration, with an owner and an expiry, before planning, and release it after checking. This is like NETCONF's `<lock>`. The second person waits or is refused.
- **Or transactions:** send the whole desired configuration in one request (or edit a candidate configuration and **commit** it), so the API applies all of it or none, atomically. Then there's no half-applied mixture to interleave with.
- **Better ACL identity:** entries addressed by a stable name the client chooses, with `PUT`, so two clients can't silently duplicate or reorder each other's entries.

On the human side, there should be **one source of truth**: `intent.yaml` in Git, reviewed and merged, with one pipeline running `sync.py`. Then two different intents never exist at the same time.

## E4.5 Where my credentials are, and how I kept them out of the repository

- **Username and password:** only in **environment variables**, `LAB_USERNAME` and `LAB_PASSWORD`, exported in the client container's shell. `sync.py` reads them with `os.environ` inside `login()`. If they're missing, it stops with `ERROR: set LAB_USERNAME and LAB_PASSWORD in the environment first`, and has no defaults to fall back on. `LAB_API_URL` is the only setting with a default (`http://api:8000`).
- **The token:** only in memory, in `self.token`, for the length of one run. A new run logs in again. The script never prints the token: the output shows only `logged in as netops` and `401: token expired, logging in again`. When it expires, `self.token` is cleared and replaced.
- **The evidence:** the helper that printed `GET /vlans` and `GET /acl/entries` kept its token in a shell `local` variable and printed only the responses. The lab scripts in `lab7/` were run with `bash -v`, so they show `$TOKEN`, never its value.
- **Idempotency keys** are printed in the plan, but they aren't secrets: a key only identifies a change.
- **The check before committing:**
  ```
  grep -rnE 'lab_[0-9a-f]{20,}|netops-pass|viewer-pass' week7/sync.py week7/intent.yaml week7/evidence week7/lab7
  ```
  This finds nothing.
  - No token (the `lab_` + 32 hex pattern) appears anywhere in the repository.
  - The lab's shared practice passwords appear only in the two **course-provided files**: `labapi.py`, the server, which defines its demo users, and the original docstring of `inventory.py`.
  - None of my own files or evidence contain a token or password.

## AI use

I used AI assistance (Claude) for this lab:
- It gave me a step-by-step guide and wrote the `bash -v` scripts that recorded Steps 2, 3 and 7.
- It diagnosed two mistakes: Step 2 failing because the `lab7` folder didn't exist yet, and Step 6's empty `expiry.txt` because I had run `inventory.py` in the laptop terminal instead of the client container.

I ran every step myself and checked the code, numbers and explanations against my own output.

For the assignment, I used AI assistance (Claude) as follows:
- It gave me a step-by-step guide.
- It wrote the helper commands that recorded `evidence/`.

I recorded all four evidence files myself on fresh APIs, and checked the answers against my own output and code.

#!/usr/bin/env python3
"""Week 8: make the lab API hold exactly what intent.yaml says - if the model accepts it.

    python3 sync.py intent.yaml --dry-run    # read the API, print the plan, change nothing
    python3 sync.py intent.yaml              # plan, apply, then read back and check the result

The gate: before anything else, the intent is checked against wic2005-intent.yang (validate.py,
with yanglint). An intent the model rejects stops here: no plan, and no request to the API.

Environment (never written in this file):
    LAB_API_URL    default http://api:8000
    LAB_USERNAME   e.g. netops
    LAB_PASSWORD

How it stays safe:
  - It reads the current state first and plans only the differences, so a second run sends no writes.
  - VLANs are written with PUT (create or rename) and DELETE: both idempotent, so safe to retry.
  - ACL entries can only be added with POST, so every POST carries an Idempotency-Key, chosen once
    per entry when the plan is made and reused on every retry of that entry.
  - 429: wait Retry-After. 503: back off and retry (the API changed nothing). 401: log in again.
    No response at all: retry only requests that are safe to repeat. 404 on DELETE: already gone.
    400/403/409/422: never retried, stop loudly.
  - After applying, it reads the state back and plans again: an empty plan means it matches.
"""
import argparse
import json
import os
import random
import sys
import time
import uuid

import requests
import yaml

from validate import validate        # the gate: wic2005-intent.yang, checked by yanglint

API = os.environ.get("LAB_API_URL", "http://api:8000").rstrip("/")
MAX_FAILURES = 6                     # 503s / lost responses in a row before giving up

sys.stdout.reconfigure(line_buffering=True)  # so output appears in order when piped to tee


class SyncError(Exception):
    """Something we must not (or can no longer) retry."""


# ---------------------------------------------------------------- talking to the API

class Client:
    """A logged-in session with the lab API that retries only what is safe to retry."""

    def __init__(self):
        self.token = None
        self.requests = 0   # every HTTP request sent to the API, retries included (logins too)
        self.logins = 0

    def login(self):
        """Get a fresh token. Logging in changes no configuration, so it is always safe to retry."""
        try:
            user, password = os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"]
        except KeyError:
            raise SyncError("set LAB_USERNAME and LAB_PASSWORD in the environment first")
        for attempt in range(1, MAX_FAILURES + 1):
            self.requests += 1
            try:
                r = requests.post(f"{API}/api/v1/auth/token", json={"username": user, "password": password},
                                  timeout=10)
            except (requests.ConnectionError, requests.Timeout) as e:
                self._backoff(attempt, f"login: no response ({type(e).__name__})")
                continue
            if r.status_code in (429, 503):
                self._backoff(attempt, f"login: {r.status_code}", r.headers.get("Retry-After"))
                continue
            if r.status_code != 200:
                raise SyncError(f"login as {user} failed: {r.status_code} {error_of(r)}")
            self.token = r.json()["token"]
            self.logins += 1
            print(f"  logged in as {user}")
            return
        raise SyncError(f"login: gave up after {MAX_FAILURES} tries")

    def send(self, method, path, body=None, key=None):
        """Send one request until it succeeds, retrying only what is safe to retry. Returns the response.

        - the body is encoded ONCE, so every retry sends identical bytes (the Idempotency-Key
          fingerprint is a hash of the raw body);
        - a lost response is retried only for GET, PUT, DELETE, or a POST carrying an Idempotency-Key;
        - 404 on DELETE counts as success: the thing is already gone (e.g. our first attempt's
          reply was lost after the server deleted it).
        """
        url = path if path.startswith("http") else API + path
        data = None if body is None else json.dumps(body, sort_keys=True).encode()
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if key:
            headers["Idempotency-Key"] = key
        safe_to_repeat = method in ("GET", "HEAD", "PUT", "DELETE") or (method == "POST" and key)
        failures = relogins = 0

        while True:
            if self.token is None:
                self.login()
            self.requests += 1
            try:
                r = requests.request(method, url, data=data, timeout=10,
                                     headers={**headers, "Authorization": f"Bearer {self.token}"})
            except (requests.ConnectionError, requests.Timeout) as e:
                if not safe_to_repeat:
                    raise SyncError(f"{method} {path}: no response, and it is NOT safe to send again "
                                    f"(the change may or may not have happened) - check the API by hand")
                failures += 1
                self._backoff(failures, f"{method} {path}: no response ({type(e).__name__})")
                continue

            if r.status_code == 401:                     # token expired: log in, same request again
                relogins += 1
                if relogins > 3:
                    raise SyncError(f"{method} {path}: still 401 after logging in again: {error_of(r)}")
                print("  401: token expired, logging in again")
                self.token = None
                continue
            if r.status_code == 429:                     # rate limited: wait what the server says
                wait = float(r.headers.get("Retry-After", "1"))
                print(f"  429: rate limited, waiting {wait:g}s")
                time.sleep(wait)
                continue
            if r.status_code == 503:                     # unavailable: nothing was changed, back off
                failures += 1
                self._backoff(failures, f"{method} {path}: 503", r.headers.get("Retry-After"))
                continue
            if r.status_code == 404 and method == "DELETE":
                return r                                 # already gone: the goal is met
            if r.ok:
                return r
            # 400, 403, 404 (not DELETE), 405, 409, 422...: our mistake; repeating it gives the same answer
            raise SyncError(f"{method} {path}: {r.status_code} {error_of(r)} (not retried)")

    def get_all(self, path):
        """GET a collection, following "next" links to the end."""
        items, url = [], path
        while url:
            body = self.send("GET", url).json()
            items.extend(body["items"])
            url = body.get("next")
        return items

    @staticmethod
    def _backoff(failures, what, retry_after=None):
        if failures >= MAX_FAILURES:
            raise SyncError(f"{what}: gave up after {MAX_FAILURES} tries")
        wait = 2 ** (failures - 1) + random.uniform(0, 0.5)
        if retry_after:
            wait = max(wait, float(retry_after))
        print(f"  {what}: try {failures} failed, retrying in {wait:.1f}s")
        time.sleep(wait)


def error_of(r):
    try:
        return r.json().get("error", r.text)
    except ValueError:
        return r.text.strip()


# ---------------------------------------------------------------- the intent

def load_intent(filename):
    """Check intent.yaml against the model BEFORE anything else: no plan, no login, no request
    to the API for an intent the model rejects. The rules live in wic2005-intent.yang; this
    only reads the intent once the model has accepted it."""
    ok, why = validate(filename)
    if not ok:
        raise SyncError(f"{filename} is rejected by the model (wic2005-intent.yang):\n    "
                        + why.replace("\n", "\n    "))
    with open(filename) as f:
        doc = yaml.safe_load(f)
    vlans = {v["id"]: v["name"] for v in doc["vlans"]}
    acl = [normalise(e) for e in doc["acl"]]
    return vlans, acl


def normalise(e):
    """An ACL entry in exactly the shape the API returns (without its id), so they compare equal."""
    return {"action": e.get("action"), "src": e.get("src"), "dst": e.get("dst"),
            "protocol": e.get("protocol", "any"), "port": e.get("port"),
            "description": e.get("description", "")}


def describe(e):
    port = "" if e["port"] is None else f"/{e['port']}"
    return f"{e['action']} {e['src']} -> {e['dst']} {e['protocol']}{port} {e['description']}"


# ---------------------------------------------------------------- read, plan, apply, check

def read_state(c):
    vlans = {v["id"]: v["name"] for v in c.get_all("/api/v1/vlans")}
    acl = c.get_all("/api/v1/acl/entries")       # in id order = the order the ACL is checked
    return vlans, acl


def make_plan(want_vlans, want_acl, have_vlans, have_acl):
    """Work out the writes that turn what the API has into what the intent wants. No writes if equal.

    Each step: (method, path, body, idempotency key or None, reason).
    """
    plan = []
    # VLANs: addressed by their own id, so PUT says "VLAN n is called X" whether or not it exists.
    for vid, name in sorted(want_vlans.items()):
        if vid not in have_vlans:
            plan.append(("PUT", f"/api/v1/vlans/{vid}", {"name": name}, None, f"create VLAN {vid} {name}"))
        elif have_vlans[vid] != name:
            plan.append(("PUT", f"/api/v1/vlans/{vid}", {"name": name}, None,
                         f"rename VLAN {vid} {have_vlans[vid]} -> {name}"))
    for vid, name in sorted(have_vlans.items()):
        if vid != 1 and vid not in want_vlans:
            plan.append(("DELETE", f"/api/v1/vlans/{vid}", None, None, f"delete VLAN {vid} {name}"))

    # ACL: entries get ids chosen by the server and can't be edited, and order matters. Keep the
    # longest prefix that already matches the intent, in order; delete everything after it; then
    # POST the rest of the intent in order. Deletes come first so the new entries end up in order.
    have = [(e["id"], normalise(e)) for e in have_acl]
    keep = 0
    while keep < min(len(have), len(want_acl)) and have[keep][1] == want_acl[keep]:
        keep += 1
    for eid, e in have[keep:]:
        plan.append(("DELETE", f"/api/v1/acl/entries/{eid}", None, None, f"delete ACL entry {eid}: {describe(e)}"))
    for e in want_acl[keep:]:
        # The key is chosen HERE, once per entry per run, before the first attempt: every retry of
        # this POST reuses it, so a lost reply can't turn into a second entry.
        key = f"sync-{uuid.uuid4()}"
        plan.append(("POST", "/api/v1/acl/entries", e, key, f"add ACL {describe(e)}"))
    return plan


def print_plan(plan):
    if not plan:
        print("Plan: nothing to do - the API already matches the intent.")
        return
    print(f"Plan: {len(plan)} change{'s' if len(plan) != 1 else ''}")
    for method, path, _, key, why in plan:
        print(f"  {method:6} {path:24} {why}" + (f"  [Idempotency-Key {key}]" if key else ""))


def main():
    ap = argparse.ArgumentParser(description="Make the lab API match an intent file.")
    ap.add_argument("intent", help="the intent file, e.g. intent.yaml")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    args = ap.parse_args()

    try:
        want_vlans, want_acl = load_intent(args.intent)
        print(f"Intent ({args.intent}): {len(want_vlans)} VLANs + VLAN 1, {len(want_acl)} ACL entries")
        c = Client()

        print(f"Reading the current state from {API} ...")
        have_vlans, have_acl = read_state(c)
        print(f"  API has {len(have_vlans)} VLANs ({', '.join(str(v) for v in sorted(have_vlans))}) "
              f"and {len(have_acl)} ACL entries")
        plan = make_plan(want_vlans, want_acl, have_vlans, have_acl)
        print_plan(plan)

        if args.dry_run:
            print(f"Dry run: nothing changed. ({c.requests} requests, all reads and logins)")
            return 0
        if not plan:
            print(f"RESULT: MATCHES - no write sent. ({c.requests} requests, {c.logins} logins)")
            return 0

        print("Applying ...")
        for i, (method, path, body, key, why) in enumerate(plan, 1):
            r = c.send(method, path, body, key)
            note = ""
            if r.headers.get("Idempotent-Replayed") == "true":
                note = " (replayed: the first attempt had worked, not applied again)"
            elif method == "DELETE" and r.status_code == 404:
                note = " (already gone: an earlier attempt had worked)"
            elif method == "PUT" and why.startswith("create") and r.status_code == 200:
                note = " (already there: an earlier attempt had worked, this PUT changed nothing)"
            print(f"  [{i}/{len(plan)}] {why}: {r.status_code}{note}")

        print("Checking: reading the state back ...")
        have_vlans, have_acl = read_state(c)
        left = make_plan(want_vlans, want_acl, have_vlans, have_acl)
        if left:
            print("RESULT: DOES NOT MATCH - still different:")
            print_plan(left)
            return 1
        print(f"RESULT: MATCHES - {len(have_vlans)} VLANs ({', '.join(str(v) for v in sorted(have_vlans))}) "
              f"and {len(have_acl)} ACL entries, exactly as {args.intent} says. "
              f"({c.requests} requests, {c.logins} logins)")
        return 0
    except SyncError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

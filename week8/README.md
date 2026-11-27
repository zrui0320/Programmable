# Week 8 Assignment: Give your intent a schema

My addresses: `NN = 40`, `NNNN = 3540`, so my block is `10.40.0.0/16`.

Week 7's `intent.yaml` kept its rules in comments, and `sync.py` checked some of them in Python. This week the rules are a YANG model, [wic2005-intent.yang](wic2005-intent.yang), and the model sits in front of the network. `sync.py` asks [validate.py](validate.py), which asks `yanglint`, before it makes a plan or sends anything to the API. An intent the model rejects never gets that far.

Everything below is real output from the `yanglab` container (pyang 2.7.1, yanglint 3.12.2), with Week 7's API (`labapi.py --id 3540`) on the `apilab` network.

| File | What it is |
|---|---|
| [wic2005-intent.yang](wic2005-intent.yang) | My model, revision 2 (2026-11-28). `pyang --strict` is clean |
| [old/wic2005-intent.yang](old/wic2005-intent.yang) | Revision 1, kept for the compatibility check |
| [scratch/wic2005-intent.yang](scratch/wic2005-intent.yang) | Revision 2 with one breaking change (VLAN range narrowed to `2..1000`), never submitted as the model |
| [intent.yaml](intent.yaml) | My intent, unchanged from Week 7 |
| [validate.py](validate.py) | The gate. I wrote `to_json()`, which only translates YAML to the model's JSON |
| [sync.py](sync.py) | Week 7's `sync.py`. `load_intent()` now calls `validate()` first, and its Python checks are gone |
| [tests/](tests/) | `good.yaml` and 20 `bad-*.yaml`, each breaking exactly one rule (its first line says which) |
| [run_tests.sh](run_tests.sh) | Runs every test through `validate.py` |
| [evidence/tests.txt](evidence/tests.txt) | G3: all 21 tests behave as expected |
| [evidence/gate.txt](evidence/gate.txt) | G3: `sync.py` stops at the model, with and without credentials |
| [evidence/update.txt](evidence/update.txt) | G4: revision 2 is compatible, and the scratch change is flagged |
| [evidence/hostbits.sh](evidence/hostbits.sh) → [evidence/hostbits.txt](evidence/hostbits.txt) | G5 question 1: `10.40.40.5/24` against my model and against the API |

## The model

```
module: wic2005-intent
  +--rw intent
     +--rw student-id-last4?   string
     +--rw vlan* [id]
     |  +--rw id      uint16
     |  +--rw name    string
     +--rw acl* [description]
        +--rw description    string
        +--rw action         enumeration
        +--rw src            own-prefix
        +--rw dst            own-prefix
        +--rw protocol?      enumeration
        +--rw port?          inet:port-number
        +--rw log?           boolean
```

Where each rule from `intent.yaml`'s comments lives:

| Rule | How the model enforces it | Test that proves it |
|---|---|---|
| VLAN ids 2 to 4094 | `type uint16 { range "2..4094"; }` | `bad-vlan-1`, `bad-vlan-4095` |
| Each VLAN listed once | `list vlan { key "id"; }`: two entries with one key are a duplicate instance | `bad-vlan-duplicate` |
| At least 6 VLANs | `min-elements 6` | `bad-five-vlans` |
| Names: 1 to 32 letters, digits, `-` or `_` | `length "1..32"` and `pattern '[A-Za-z0-9_-]+'` | `bad-vlan-name-space`, `bad-vlan-name-33` |
| At least 4 ACL entries | `min-elements 4` | `bad-three-acl` |
| Every entry has a description, and it's unique (new) | `list acl { key "description"; }`: a key is always required and always unique | `bad-no-description`, `bad-duplicate-description` |
| Addresses only from `10.40.0.0/16`, always with a length | typedef `own-prefix`: `inet:ipv4-prefix` plus `pattern '10\.40\.[0-9]+\.[0-9]+/(1[6-9]\|2[0-9]\|3[0-2])'` | `bad-other-block`, `bad-wider-than-block`, `bad-no-prefix-length` |
| At least one permit, one deny, one entry with a port | three `must`s on the container, with `count()`, e.g. `count(acl[action = 'deny']) >= 1` | `bad-no-permit`, `bad-no-deny`, `bad-no-port` |
| A port only on `tcp` or `udp` entries | `when "../protocol = 'tcp' or ../protocol = 'udp'"` on `port` | `bad-port-icmp`, `bad-port-default-any` |
| Ports 1 to 65535 | `type inet:port-number { range "1..65535"; }` | `bad-port-0`, `bad-port-65536` |
| `protocol` is tcp, udp, icmp or any, and `any` if not given | `enumeration` with `default "any"` | `bad-protocol-gre`, `bad-port-default-any` |

`ordered-by user` keeps the ACL in the order it's written, because an ACL is checked top to bottom. `bad-port-default-any` puts a port on an entry with no `protocol` at all: it's rejected, which shows the default `any` takes part in the `when` check.

## G5: Questions about my own model

### 1. Which rules could I not express in YANG, or only awkwardly? Where should they live instead?

**Couldn't express: "a prefix is a network, not a host with a length".** `10.40.40.5/24` has host bits set: the network is `10.40.40.0/24`. My model **accepts** it, and the lab API **rejects** it ([evidence/hostbits.txt](evidence/hostbits.txt)):

```
$ python3 validate.py /tmp/hostbits.yaml          # my model
VALID: /tmp/hostbits.yaml matches wic2005-intent.yang

$ POST /api/v1/acl/entries {"src": "10.40.40.5/24", ...}   # the lab API
422 Unprocessable Entity {"error": "src must be a network in CIDR form, such as 10.0.10.0/24"}
ACL entries on the API afterwards: 0 (nothing was added)
```

`inet:ipv4-prefix` and my `pattern` only look at the text: four numbers and a length. Whether the bits after the length are zero is arithmetic on the address, and XPath 1.0 has no bit operations. The API checks it with `ipaddress.ip_network(v, strict=True)`.

**Only awkwardly:**

- **The block check is a regex, not a containment test.** "Inside `10.40.0.0/16`" became "starts with `10.40.` and has a length of 16 or more". That only works because my block ends on an octet boundary. A block such as `10.40.128.0/17` would need a much uglier pattern.
- **"At least one permit / deny / port"** needed a `must` with `count()` on the container. The rule is about the whole list, so it can't sit on any one entry.
- **"Unique description"** is done by making `description` the list key. That works, but it means a description is now the entry's identity: renaming one is a delete and a create, not an edit. `unique "description"` on a list with another key is the alternative.
- **Some rules can't be written at all.** "No entry is shadowed by an earlier, wider one", or "src and dst are different subnets", compare addresses arithmetically across entries, which XPath can't do.

**Where they should live:** in the server that owns the configuration, here the API (on a real device, the NETCONF/RESTCONF server). Only the server is in front of *every* client: my `sync.py`, someone's curl, another team's tool. It also has real address arithmetic and the device's actual state. My model is the first gate, it's cheap and it catches most mistakes before any request, but it isn't the last one. The server's checks are.

### 2. How would one model, shared by the whole class, express "addresses from the owner's block"?

Take the block out of the pattern and work it out from the data. Each intent already says whose it is, in `student-id-last4`. The shared model keeps a general pattern (`10.x.x.x/16..32`) and adds a `must` on `src` and `dst` that builds the owner's `10.NN.` from the last two digits:

```yang
leaf src {
  type own-prefix;              // now just '10\.[0-9]+\.[0-9]+\.[0-9]+/(1[6-9]|2[0-9]|3[0-2])'
  must "starts-with(., concat('10.', number(substring(/wi:intent/wi:student-id-last4, 3, 2)), '.'))" {
    error-message "src must be inside the owner's block, 10.NN.0.0/16.";
  }
}
```

`number()` turns `"07"` into `7`, so it follows the course's rule that an ID ending 4207 owns `10.7.0.0/16`. I tested this on a copy of my module (it's `pyang --strict` clean):

| Intent | Result |
|---|---|
| `3540`, addresses `10.40.…` (mine) | VALID |
| `3540`, one `src` in `10.41.…` | INVALID: src must be inside the owner's block |
| `4207`, addresses `10.7.…` | VALID |
| `4207`, addresses `10.40.…` | INVALID: src must be inside the owner's block |

The model then holds the *rule*, and the data holds the *owner*. For that, `student-id-last4` must become `mandatory true`, which is a breaking change for old intents without it, so it would be a new revision. A cleaner alternative is an explicit `leaf block { type inet:ipv4-prefix; mandatory true; }` in each intent. But a containment check against it hits the same XPath arithmetic limit as question 1, so in practice the server checks it.

### 3. In Week 7's lab API, which data would be `config false`?

Anything the server decides, measures or keeps by itself, rather than what a client sets. From `labapi.py`:

1. **ACL entry `id`s.** The server assigns them from `next_acl` on every POST, and a client can't choose or change them.
2. **The devices inventory** (`/api/v1/devices`: `name`, `model`, `mgmt_ip`, `os_version`). It's GET-only. It describes what exists, and nobody configures it through this API.
3. **The audit log** (`/api/v1/lab/audit`: `seq`, `time`, `status`, `change`). It's a record of what the server applied.
4. **Session data:** a token's `expires_in` and `role`, the Idempotency-Key records, and the rate-limit buckets.

VLAN 1 `default` is a special case. It's configuration, but the *system* creates it, not the client. NMDA calls that the `<system>` origin. `sync.py` already never deletes it.

### 4. If the API validated against my model, what would its 422 tell the client that it can't now?

Today, `PUT /api/v1/vlans/5000` gives `422 {"error": "id must be a VLAN number from 1 to 4094"}`: a sentence for a human, and only the first problem it found. Validated against my model, a RESTCONF-style error (RFC 8040) would say:

- **Where:** `error-path` = `/wic2005-intent:intent/vlan/id` (with the list entry's key when there is one, e.g. `/wic2005-intent:intent/vlan[id='10']/name`), the exact node, in the same path language as the model. A client can point at the field rather than parse English.
- **What kind of failure:** `error-tag` = `invalid-value`, and for my `must`s an `error-app-tag`, a fixed machine-readable reason (e.g. `no-deny-entry`) that a client can switch on.
- **Which rule:** the message is the model's own `error-message`, and the rule is in a document the client can read in advance. The same model can check the intent *before* sending, as `validate.py` does, so client and server can't disagree about what's valid.
- **Every problem at once:** yanglint reports several errors in one run, where the API's `check_acl()` returns only the first problem it finds.

### 5. Which change in G4 would break my `sync.py`, or someone else's client, and why didn't mine break?

**The scratch change breaks clients.** It narrows `range "2..4094"` to `range "2..1000"`. Any intent that was valid yesterday with VLAN 1001 to 4094 is invalid today, with nothing changed on the client's side. pyang caught it ([evidence/update.txt](evidence/update.txt)):

```
scratch/wic2005-intent.yang:61: error: the range has been illegally restricted (RFC 7950: sec. 11, p5, bullet 3)
```

My own intent only uses VLANs 10 to 60, so it would pass. But a model is shared, and someone else's network is the one that breaks.

**Revision 2 (`log`) breaks nobody.** The new leaf is optional with a default (`false`), so every intent that was valid stays valid: pyang says `COMPATIBLE`, and `run_tests.sh` still passes all 21 tests on revision 2.

**My `sync.py` in particular** doesn't break for two reasons:
- **`to_json()`** copies the YAML across as it is, so an intent without `log` still produces valid JSON.
- **`normalise()`** builds each ACL entry from the fields it knows (`action`, `src`, `dst`, `protocol`, `port`, `description`), so a `log` in the intent can't change what it sends.

There's a downside: if I set `log: true`, the model accepts it but `sync.py` silently doesn't send it. Those are two places that have to learn about a new field. The changes that *would* break `sync.py` are the ones it depends on by name: removing or renaming `vlan`, `acl`, `id`, `name` or `description` (`load_intent()` reads them directly), or turning an optional field mandatory, which `--check-update-from` also reports as illegal.

### 6. A measured value per entry, such as round-trip time: configuration or state?

**State.** Nobody sets a round-trip time; the network measures it, and it changes on its own. In YANG it's marked `config false`, inside the entry it belongs to:

```yang
leaf rtt {
  config false;
  type uint32;
  units "microseconds";
  description "Measured round-trip time to the entry's server.";
}
```

`pyang -f tree` would show it as `+--ro rtt?`. Under NMDA it's never in `<running>` or `<candidate>`, and `yanglint -t config` would reject it there, as `clients` was rejected in the lab's Step 6. You read it from **`<operational>`**, which holds the configuration in use *plus* state like this, side by side in one tree.

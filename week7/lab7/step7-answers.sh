# Step 7: answers read from the OpenAPI document alone (lab7/openapi.json)
python3 - <<'PY'
import json
d = json.load(open("lab7/openapi.json"))
p = d["paths"]
lim = next(x for x in p["/api/v1/devices"]["get"]["parameters"] if x["name"] == "limit")["schema"]
print("1. devices limit:", lim)
print("2. /api/v1/acl/entries/{id} methods:", [m.upper() for m in p["/api/v1/acl/entries/{id}"] if m != "parameters"])
key = next(x for x in p["/api/v1/acl/entries"]["post"]["parameters"] if x["name"] == "Idempotency-Key")
print("3. Idempotency-Key:", key["description"])
site = next(x for x in p["/api/v1/devices"]["get"]["parameters"] if x["name"] == "site")
print("4. site filter:", site["description"], site["schema"]["enum"])
print("All paths:", {k: [m.upper() for m in v if m != "parameters"] for k, v in p.items()})
PY

#!/bin/sh
# G5 question 1: a prefix with host bits set, 10.40.40.5/24, against my model and against the lab API.
# Run in yanglab from week8/, with LAB_USERNAME and LAB_PASSWORD set. The API refuses the entry with
# a 422, so nothing on the API changes.
cd "$(dirname "$0")/.."
sed 's#src: 10.40.10.0/24#src: 10.40.40.5/24#' intent.yaml > /tmp/hostbits.yaml

echo '$ grep 10.40.40.5 /tmp/hostbits.yaml'
grep 10.40.40.5 /tmp/hostbits.yaml
echo
echo '$ python3 validate.py /tmp/hostbits.yaml          # my model'
python3 validate.py /tmp/hostbits.yaml
echo "(exit $?)"
echo
echo '$ POST /api/v1/acl/entries {"src": "10.40.40.5/24", ...}   # the lab API'
python3 - <<'EOF'
import os, requests
api = os.environ.get("LAB_API_URL", "http://api:8000")
tok = requests.post(f"{api}/api/v1/auth/token",
                    json={"username": os.environ["LAB_USERNAME"], "password": os.environ["LAB_PASSWORD"]}).json()["token"]
r = requests.post(f"{api}/api/v1/acl/entries", headers={"Authorization": f"Bearer {tok}"},
                  json={"action": "permit", "src": "10.40.40.5/24", "dst": "10.40.20.0/24",
                        "protocol": "tcp", "port": 443, "description": "host-bits-test"})
print(r.status_code, r.reason, r.text.strip())
n = len(requests.get(f"{api}/api/v1/acl/entries", headers={"Authorization": f"Bearer {tok}"}).json()["items"])
print(f"ACL entries on the API afterwards: {n} (nothing was added)")
EOF
rm -f /tmp/hostbits.yaml

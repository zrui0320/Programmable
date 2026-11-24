# Step 7: list the devices at one site (penang) with the site filter found in the OpenAPI document
API=http://api:8000/api/v1
TOKEN=$(curl -s -X POST $API/auth/token -H 'Content-Type: application/json' -d "{\"username\": \"$LAB_USERNAME\", \"password\": \"$LAB_PASSWORD\"}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
# page 1: limit 50 (the documented maximum); headers show X-Total-Count for this site only
curl -s -D - "$API/devices?site=penang&limit=50" -H "Authorization: Bearer $TOKEN" -o page1.json | grep -iE '^HTTP|^X-Total-Count'
python3 -c 'import json; b=json.load(open("page1.json")); [print(d["name"], d["site"], d["mgmt_ip"], d["model"]) for d in b["items"]]; print("next:", b["next"])'
sleep 1
# page 2: follow the next URL exactly as given (it keeps site=penang)
NEXT=$(python3 -c 'import json; print(json.load(open("page1.json"))["next"])')
curl -s -D - "$NEXT" -H "Authorization: Bearer $TOKEN" -o page2.json | grep -iE '^HTTP|^X-Total-Count'
python3 -c 'import json; b=json.load(open("page2.json")); [print(d["name"], d["site"], d["mgmt_ip"], d["model"]) for d in b["items"]]; print("next:", b["next"])'
python3 -c 'import json; n=sum(len(json.load(open(f))["items"]) for f in ("page1.json","page2.json")); print("penang devices listed:", n)'
rm -f page1.json page2.json

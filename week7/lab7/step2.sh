API=http://api:8000/api/v1
AUTH="Authorization: Bearer $TOKEN"
VAUTH="Authorization: Bearer $VIEWER_TOKEN"
JSON='Content-Type: application/json'

# 401: no token
curl -si $API/vlans; echo; sleep 1
# 404: VLAN that doesn't exist
curl -si $API/vlans/77 -H "$AUTH"; echo; sleep 1
# 405: POST to a single VLAN
curl -si -X POST $API/vlans/10 -H "$AUTH" -H "$JSON" -d '{"name": "x"}'; echo; sleep 1
# 400: broken JSON
curl -si -X POST $API/vlans -H "$AUTH" -H "$JSON" -d '{"id": 30, "name": guest}'; echo; sleep 1
# 422: VLAN out of range
curl -si -X PUT $API/vlans/5000 -H "$AUTH" -H "$JSON" -d '{"name": "too-big"}'; echo; sleep 1
# 409: VLAN 10 already exists
curl -si -X POST $API/vlans -H "$AUTH" -H "$JSON" -d '{"id": 10, "name": "dup"}'; echo; sleep 1
# 403: viewer tries to write
curl -si -X PUT $API/vlans/30 -H "$VAUTH" -H "$JSON" -d '{"name": "guests"}'; echo; sleep 1
# 429: 10 GETs in a row, limit is 5/s
for i in $(seq 10); do curl -s -o /dev/null -w "%{http_code} " $API/vlans -H "$AUTH"; done; echo

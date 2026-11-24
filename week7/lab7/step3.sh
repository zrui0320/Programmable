API=http://api:8000/api/v1
AUTH="Authorization: Bearer $TOKEN"
JSON='Content-Type: application/json'
ACL='{"action": "deny", "src": "10.40.30.0/24", "dst": "10.40.20.0/24", "protocol": "tcp", "port": 22, "description": "guests-no-ssh"}'

# PUT VLAN 30, twice
curl -si -X PUT $API/vlans/30 -H "$AUTH" -H "$JSON" -d '{"name": "guests"}' | head -1; sleep 1
curl -si -X PUT $API/vlans/30 -H "$AUTH" -H "$JSON" -d '{"name": "guests"}' | head -1; sleep 1

# DELETE VLAN 99, twice
curl -si -X DELETE $API/vlans/99 -H "$AUTH" | head -1; sleep 1
curl -si -X DELETE $API/vlans/99 -H "$AUTH" | head -1; sleep 1

# POST the ACL entry, twice, no key
curl -si -X POST $API/acl/entries -H "$AUTH" -H "$JSON" -d "$ACL" | head -1; sleep 1
curl -si -X POST $API/acl/entries -H "$AUTH" -H "$JSON" -d "$ACL" | head -1; sleep 1

# POST the ACL entry, twice, WITH the same Idempotency-Key
curl -si -X POST $API/acl/entries -H "$AUTH" -H "$JSON" -H 'Idempotency-Key: guests-no-ssh-1' -d "$ACL" | grep -E '^HTTP|^Idempotent|^Location'; sleep 1
curl -si -X POST $API/acl/entries -H "$AUTH" -H "$JSON" -H 'Idempotency-Key: guests-no-ssh-1' -d "$ACL" | grep -E '^HTTP|^Idempotent|^Location'

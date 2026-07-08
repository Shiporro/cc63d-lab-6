#!/bin/bash
# Seed data for the incident management platform
# Run with: bash seed.sh

BASE_URL="${1:-http://localhost:8000}"

echo "=== Seeding services ==="
curl -s -X POST "$BASE_URL/services" -H "Content-Type: application/json" \
  -d '{"name": "payments-api", "team": "payments", "slo_target": 99.95, "sli_type": "availability"}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/services" -H "Content-Type: application/json" \
  -d '{"name": "user-auth", "team": "identity", "slo_target": 99.99, "sli_type": "availability"}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/services" -H "Content-Type: application/json" \
  -d '{"name": "search-index", "team": "discovery", "slo_target": 99.9, "sli_type": "latency"}' | python3 -m json.tool

echo ""
echo "=== Setting up on-call schedules ==="
curl -s -X POST "$BASE_URL/oncall" -H "Content-Type: application/json" \
  -d '{"service_id": 1, "person": "Ana García", "email": "ana@example.com", "start_date": "2026-05-01", "end_date": "2026-08-31"}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/oncall" -H "Content-Type: application/json" \
  -d '{"service_id": 2, "person": "Carlos Muñoz", "email": "carlos@example.com", "start_date": "2026-05-01", "end_date": "2026-08-31"}' | python3 -m json.tool

echo ""
echo "=== Creating an incident ==="
curl -s -X POST "$BASE_URL/incidents" -H "Content-Type: application/json" \
  -d '{"service_id": 1, "title": "High error rate on payment processing", "severity": 2, "created_by": "monitoring"}' | python3 -m json.tool

echo ""
echo "=== Updating incident timeline ==="
curl -s -X PATCH "$BASE_URL/incidents/1" -H "Content-Type: application/json" \
  -d '{"status": "investigating", "author": "Ana García", "message": "Investigating spike in 5xx errors since 14:30 UTC"}' | python3 -m json.tool

curl -s -X PATCH "$BASE_URL/incidents/1" -H "Content-Type: application/json" \
  -d '{"author": "Ana García", "message": "Root cause identified: connection pool exhausted due to slow DB queries"}' | python3 -m json.tool

curl -s -X PATCH "$BASE_URL/incidents/1" -H "Content-Type: application/json" \
  -d '{"status": "mitigated", "author": "Ana García", "message": "Increased connection pool size from 10 to 50, error rate dropping"}' | python3 -m json.tool

curl -s -X PATCH "$BASE_URL/incidents/1" -H "Content-Type: application/json" \
  -d '{"status": "resolved", "author": "Ana García", "message": "Error rate back to normal levels. Total duration: 45 minutes"}' | python3 -m json.tool

echo ""
echo "=== Writing post-mortem ==="
curl -s -X POST "$BASE_URL/postmortems" -H "Content-Type: application/json" \
  -d '{
    "incident_id": 1,
    "author": "Ana García",
    "summary": "Payment processing experienced elevated error rates (15% 5xx) for 45 minutes due to database connection pool exhaustion.",
    "root_cause": "A slow query introduced in deploy v2.3.1 caused connections to be held longer than expected. Under normal load this was not noticeable, but during the Friday afternoon peak the pool was exhausted.",
    "impact": "Approximately 2,300 payment attempts failed (3.2% of daily volume). SLO budget consumed: 12 minutes of the monthly 21.6 minute budget (99.95% target).",
    "action_items": "1. Add connection pool metrics to monitoring dashboard. 2. Set up alerting on pool utilization > 80%. 3. Add query timeout of 5s. 4. Review slow query in v2.3.1 and optimize.",
    "lessons": "Our connection pool size was set to the default value and never reviewed. We need to load-test configuration changes, not just code changes."
  }' | python3 -m json.tool

echo ""
echo "=== Verifying: get incident with full timeline ==="
curl -s "$BASE_URL/incidents/1" | python3 -m json.tool

echo ""
echo "=== Done! ==="
echo "Try: curl $BASE_URL/services"
echo "Try: curl $BASE_URL/incidents"
echo "Try: curl $BASE_URL/postmortems"

# Audit Trail Reconciliation (TK-CAF42C)

## Overview

The AI API Platform maintains a comprehensive audit trail of all LLM requests in PostgreSQL. When database writes fail (connection issues, outages, schema migrations), request data is preserved in Loki structured logs with `event=audit_write_failure`. This system automatically detects gaps and provides tools to recover missing records.

## Architecture

```
┌─────────────┐
│ LLM Request │
└──────┬──────┘
       │
       v
┌──────────────────────┐
│ Audit Middleware     │
│ (audit.py)           │
└──────┬───────────────┘
       │
       ├─────────────────┐
       v                 v
┌─────────────┐   ┌────────────────┐
│ PostgreSQL  │   │ Loki           │
│ llm_requests│   │ (on failure)   │
└─────────────┘   └────────────────┘
       │                 │
       v                 v
┌─────────────┐   ┌────────────────┐
│ Prometheus  │   │ Reconciliation │
│ Alerts      │   │ Script         │
└─────────────┘   └────────────────┘
```

## How It Works

### 1. Normal Operation

1. Request proxied to OpenAI API
2. Response returned to customer
3. Audit middleware writes to PostgreSQL `llm_requests` table
4. Prometheus counter `postgres_audit_records_inserted` incremented
5. Done ✓

### 2. Database Failure

1. Request proxied to OpenAI API
2. Response returned to customer ✓ (customer not impacted)
3. Audit middleware PostgreSQL write **fails**
4. Complete audit data logged to Loki with `event=audit_write_failure`
5. Prometheus counter `audit_write_failures_total` incremented
6. Alert fires if failure rate > threshold

### 3. Automatic Detection

Prometheus monitors the gap between total requests and successful writes:

```promql
abs(
  sum(increase(llm_requests_total[1h]))
  -
  sum(increase(postgres_audit_records_inserted[1h]))
) > 10
```

If gap exceeds 10 records over 1 hour → `AuditDataIntegrityGap` alert fires.

### 4. Recovery

Run reconciliation script to recover missing records from Loki:

```bash
# Recover last 24 hours
python /scripts/reconcile_audit_trail.py --since 24h

# Recover specific time range
python /scripts/reconcile_audit_trail.py \
  --start "2026-03-28T00:00:00Z" \
  --end "2026-03-28T23:59:59Z"

# Dry run (no database writes)
python /scripts/reconcile_audit_trail.py --since 24h --dry-run
```

## Alerts

### AuditWriteFailure (Warning)
- **Trigger**: Any audit write fails
- **Action**: Check Loki logs for root cause
- **Impact**: No customer impact (data preserved in logs)

### AuditWriteFailureRate (Critical)
- **Trigger**: >1% failure rate for 2+ minutes
- **Action**: Investigate database health, run reconciliation
- **Impact**: Data integrity at risk

### AuditDataIntegrityGap (Warning)
- **Trigger**: >10 records gap between requests and audit records
- **Action**: Run reconciliation script
- **Impact**: Billing/analytics may be incomplete

### DatabaseConnectionPoolExhausted (Warning)
- **Trigger**: <2 available connections
- **Action**: Scale up database or reduce connection usage
- **Impact**: May cause audit write failures

## Runbook: Responding to Audit Failures

### Scenario 1: Single Audit Write Failure

```bash
# 1. Check alert for customer_id and timestamp
# Alert shows: AuditWriteFailure for CUST-001 at 2026-03-28T15:30:00Z

# 2. Query Loki for the failed record
curl -G "http://loki:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={event="audit_write_failure", customer_id="CUST-001"}' \
  --data-urlencode 'start=2026-03-28T15:29:00Z' \
  --data-urlencode 'end=2026-03-28T15:31:00Z'

# 3. Run reconciliation for that time window
python /scripts/reconcile_audit_trail.py \
  --start "2026-03-28T15:29:00Z" \
  --end "2026-03-28T15:31:00Z" \
  --customer-id CUST-001

# 4. Verify record was recovered
psql -c "SELECT * FROM llm_requests WHERE customer_id='CUST-001' AND timestamp='2026-03-28T15:30:00Z'"
```

### Scenario 2: Database Outage

```bash
# 1. Database was down from 14:00 to 14:30

# 2. After database recovery, run reconciliation
python /scripts/reconcile_audit_trail.py \
  --start "2026-03-28T14:00:00Z" \
  --end "2026-03-28T14:30:00Z"

# Expected output:
#   Found 247 failed audit writes in Loki logs
#   Recovered: 247
#   Already existed: 0
#   Failed: 0

# 3. Verify metrics gap closed
curl http://prometheus:9090/api/v1/query?query='abs(sum(increase(llm_requests_total[1h]))-sum(increase(postgres_audit_records_inserted[1h])))'
```

### Scenario 3: Ongoing High Failure Rate

```bash
# 1. Check database health
psql -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"

# 2. Check connection pool metrics
curl http://localhost:9090/metrics | grep db_connection_pool

# 3. If pool exhausted, scale up
kubectl scale deployment ai-api-platform --replicas=4

# 4. If database slow, check slow query log
psql -c "SELECT query, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;"

# 5. Once resolved, run reconciliation for the outage window
```

## Scheduled Reconciliation

For production, run periodic reconciliation via cron to catch any gaps:

```bash
# Daily reconciliation at 2 AM UTC
0 2 * * * /usr/bin/python /scripts/reconcile_audit_trail.py --since 24h >> /var/log/audit_reconciliation.log 2>&1
```

This provides defense-in-depth: alerts catch issues immediately, scheduled reconciliation catches anything that slipped through.

## Metrics to Monitor

| Metric | Threshold | Action |
|--------|-----------|--------|
| `audit_write_failures_total` | >0 | Investigate root cause |
| `rate(audit_write_failures_total[5m])` | >0.01 | Critical issue - run reconciliation |
| `llm_requests_total - postgres_audit_records_inserted` | >10 over 1h | Run reconciliation |
| `db_connection_pool_available` | <2 | Scale database or reduce load |
| `redis_budget_update_failures_total` | >0 | Check Redis health |

## Data Integrity Guarantees

1. **Customer requests never fail due to audit failures**
   - Audit happens after response is sent
   - Errors are logged but not propagated

2. **All request data is preserved**
   - Failed writes → Loki structured logs with full `audit_data`
   - Complete record available for reconstruction

3. **Reconciliation is idempotent**
   - Uses `INSERT ... ON CONFLICT DO NOTHING`
   - Safe to run multiple times on same time range

4. **Gaps are automatically detected**
   - Prometheus compares request count vs audit record count
   - Alerts fire when gap exceeds threshold

## Testing

To test the reconciliation system in staging:

```bash
# 1. Simulate database failure with Chaos Mesh
kubectl apply -f k8s/chaos/postgres-partition.yaml

# 2. Send test requests while database is down
for i in {1..50}; do
  curl -X POST http://staging-api/v1/llm/proxy \
    -H "X-API-Key: test-key-001" \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]}'
done

# 3. Verify audit_write_failures metric increased
curl http://prometheus:9090/api/v1/query?query='audit_write_failures_total'

# 4. Restore database
kubectl delete -f k8s/chaos/postgres-partition.yaml

# 5. Run reconciliation
python /scripts/reconcile_audit_trail.py --since 5m --dry-run

# 6. Verify all 50 records found in Loki
# Then run without --dry-run to recover

# 7. Verify gap is closed
```

## Production Readiness Checklist

- [x] Prometheus metrics instrumented in audit middleware
- [x] Alert rules configured in `monitoring/alerts.yml`
- [x] AlertManager configured to route to PagerDuty
- [x] Reconciliation script with dry-run mode
- [x] Structured logging with complete audit_data on failures
- [x] Idempotent INSERT with ON CONFLICT DO NOTHING
- [ ] Scheduled cron job for daily reconciliation
- [ ] Runbook tested in staging with simulated failures
- [ ] Team training on reconciliation procedures
- [ ] Grafana dashboard showing audit metrics
- [ ] PagerDuty integration tested

## Future Enhancements

1. **Real-time reconciliation**: Run reconciliation automatically when alerts fire
2. **Customer-level SLAs**: Alert per-customer if their audit gap exceeds threshold
3. **Long-term archival**: Move old audit records to S3/BigQuery for cost savings
4. **Audit trail API**: Expose audit query endpoint for customer self-service

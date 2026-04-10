# Monitoring & Alerting Stack

Comprehensive monitoring solution for AI API Platform audit trail integrity (TK-CAF42C).

## Components

| Service | Port | Purpose |
|---------|------|---------|
| **Prometheus** | 9091 | Metrics collection, alert evaluation |
| **AlertManager** | 9093 | Alert routing (PagerDuty, Slack) |
| **Loki** | 3100 | Structured log aggregation for audit recovery |
| **Grafana** | 3000 | Dashboards and visualization |

## Quick Start

### 1. Set up environment variables

```bash
# Create .env file in project root
cat > .env <<EOF
# Required for production
PAGERDUTY_SERVICE_KEY=your-integration-key-here
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL

# Optional overrides
LOKI_URL=http://loki:3100
EOF
```

### 2. Start the monitoring stack

```bash
# Start all services
docker-compose up -d

# Verify services are running
docker-compose ps

# Check Prometheus targets
curl http://localhost:9091/api/v1/targets | jq

# Check AlertManager status
curl http://localhost:9093/api/v1/status
```

### 3. Access dashboards

- **Prometheus**: http://localhost:9091
- **AlertManager**: http://localhost:9093
- **Grafana**: http://localhost:3000 (admin/admin)
- **Loki**: http://localhost:3100

## Prometheus Metrics

### Audit Trail Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `audit_write_failures_total` | Counter | - | Failed PostgreSQL audit writes |
| `redis_budget_update_failures_total` | Counter | - | Failed Redis budget updates |
| `llm_requests_total` | Counter | `customer_id` | Total LLM requests processed |
| `postgres_audit_records_inserted` | Counter | `customer_id` | Successful audit record writes |
| `db_connection_pool_size` | Gauge | - | Total DB connection pool size |
| `db_connection_pool_available` | Gauge | - | Available DB connections |

### Request Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `api_requests_total` | Counter | `customer_id, model, status` | Total API requests |
| `api_request_duration_seconds` | Histogram | `customer_id, model` | Request latency |
| `api_request_cost_usd` | Counter | `customer_id, model` | Total cost in USD |
| `api_tokens_total` | Counter | `customer_id, model, direction` | Token counts |
| `api_requests_throttled_total` | Counter | `customer_id, reason` | Throttled requests |

## Alert Rules

### Critical Alerts (PagerDuty)

#### AuditWriteFailureRate
```yaml
expr: rate(audit_write_failures_total[5m]) > 0.01
for: 2m
```
**Action**: Check database health, run reconciliation script

#### AuditDataIntegrityGap
```yaml
expr: abs(sum(increase(llm_requests_total[1h])) - sum(increase(postgres_audit_records_inserted[1h]))) > 10
for: 5m
```
**Action**: Run `/scripts/reconcile_audit_trail.py --since 1h`

### Warning Alerts (Slack)

#### AuditWriteFailure
Fires on first audit failure. Informational - no immediate action required.

#### RedisBudgetUpdateFailureRate
Check Redis health if rate exceeds 5% for 2+ minutes.

#### DatabaseConnectionPoolExhausted
Scale database or reduce load when <2 connections available.

## AlertManager Configuration

### Routing Rules

```
Critical (severity=critical) → PagerDuty (immediate)
Warning (severity=warning)   → Slack #devops (30s delay)
Default                      → Slack #devops
```

### Testing Alerts

```bash
# Fire a test alert
curl -X POST http://localhost:9093/api/v1/alerts -d '[
  {
    "labels": {
      "alertname": "TestAlert",
      "severity": "warning"
    },
    "annotations": {
      "summary": "This is a test alert"
    }
  }
]'

# Check active alerts
curl http://localhost:9093/api/v1/alerts | jq
```

## Loki Configuration

### Log Retention

Audit logs retained for **30 days** to support reconciliation.

```yaml
limits_config:
  retention_period: 720h  # 30 days
```

### Querying Audit Failures

```bash
# Query audit failures for specific customer
curl -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={event="audit_write_failure", customer_id="CUST-001"}' \
  --data-urlencode 'start=2026-03-28T00:00:00Z' \
  --data-urlencode 'end=2026-03-28T23:59:59Z' | jq

# Extract audit_data from logs
curl -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={event="audit_write_failure"} | json | line_format "{{.audit_data}}"' \
  | jq -r '.data.result[].values[][1]'
```

## Grafana Dashboards

### 1. Audit Trail Health Dashboard

**Panels:**
- Request vs Audit Record Count (line graph)
- Audit Failure Rate (gauge)
- Data Integrity Gap (single stat)
- Redis Budget Update Failures (line graph)
- Database Connection Pool Usage (gauge)

**Import JSON:** (TODO: create dashboard JSON)

### 2. Customer Usage Dashboard

**Panels:**
- Requests per customer (bar chart)
- Cost per customer (table)
- Token usage by model (stacked area)
- Throttled requests (heatmap)

## Production Deployment

### Kubernetes

The monitoring stack can be deployed to Kubernetes alongside the API:

```bash
# Deploy Prometheus with persistent storage
kubectl apply -f k8s/monitoring/prometheus-deployment.yaml
kubectl apply -f k8s/monitoring/prometheus-pvc.yaml

# Deploy AlertManager
kubectl apply -f k8s/monitoring/alertmanager-deployment.yaml
kubectl apply -f k8s/monitoring/alertmanager-config.yaml  # ConfigMap with alerts

# Deploy Loki
kubectl apply -f k8s/monitoring/loki-deployment.yaml
kubectl apply -f k8s/monitoring/loki-pvc.yaml

# Deploy Grafana
kubectl apply -f k8s/monitoring/grafana-deployment.yaml
```

### Secrets

Create Kubernetes secrets for sensitive config:

```bash
# PagerDuty integration key
kubectl create secret generic pagerduty-key \
  --from-literal=service-key=YOUR_KEY_HERE

# Slack webhook
kubectl create secret generic slack-webhook \
  --from-literal=url=https://hooks.slack.com/services/YOUR/WEBHOOK
```

Reference in AlertManager ConfigMap:
```yaml
receivers:
  - name: 'pagerduty-critical'
    pagerduty_configs:
      - service_key: ${PAGERDUTY_SERVICE_KEY}
```

## Backup & Disaster Recovery

### Prometheus Data

```bash
# Backup Prometheus TSDB
docker run --rm -v prometheus-data:/data -v $(pwd):/backup \
  alpine tar czf /backup/prometheus-backup-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm -v prometheus-data:/data -v $(pwd):/backup \
  alpine tar xzf /backup/prometheus-backup-20260328.tar.gz -C /
```

### Loki Logs

Loki data is in `loki-data` volume. For production, configure S3/GCS backend:

```yaml
# loki-config.yml
storage_config:
  aws:
    s3: s3://us-east-1/my-loki-bucket
    dynamodb:
      dynamodb_url: dynamodb://us-east-1
```

## Troubleshooting

### Prometheus not scraping metrics

```bash
# Check targets
curl http://localhost:9091/api/v1/targets | jq '.data.activeTargets[] | {job, health, lastError}'

# Common issues:
# 1. API service not exposing /metrics endpoint
# 2. Network connectivity (check docker network)
# 3. Firewall blocking port 9090
```

### AlertManager not sending alerts

```bash
# Check AlertManager logs
docker-compose logs alertmanager

# Verify SMTP/PagerDuty config
curl http://localhost:9093/api/v1/status | jq

# Test alert routing
amtool --alertmanager.url=http://localhost:9093 alert add \
  alertname=test severity=critical
```

### Loki query timeout

```bash
# Check Loki limits
curl http://localhost:3100/config | jq '.limits_config'

# Reduce query time range or add more specific label filters
# Bad:  {job="api"}
# Good: {job="api", customer_id="CUST-001", event="audit_write_failure"}
```

## Performance Tuning

### Prometheus

```yaml
# Increase retention for long-term analysis
command:
  - '--storage.tsdb.retention.time=90d'
  - '--storage.tsdb.retention.size=50GB'

# Reduce scrape interval for lower load
scrape_interval: 30s
```

### Loki

```yaml
# Limit query results
limits_config:
  max_query_length: 721h  # 30 days
  max_query_series: 500
  max_entries_limit_per_query: 5000
```

## Cost Optimization

| Service | Resource Usage | Cost Reduction |
|---------|---------------|----------------|
| Prometheus | ~500MB RAM, 10GB disk | Reduce retention to 30d |
| Loki | ~1GB RAM, 50GB disk | Enable compression, S3 backend |
| Grafana | ~200MB RAM | Use read-only datasources |
| AlertManager | ~100MB RAM | Minimal cost |

**Total**: ~2GB RAM, 60GB disk for 30-day retention with 5 customers at 10K requests/day.

## Related Documentation

- [Audit Reconciliation Guide](../docs/AUDIT_RECONCILIATION.md)
- [Prometheus Alert Best Practices](https://prometheus.io/docs/practices/alerting/)
- [Loki Query Language](https://grafana.com/docs/loki/latest/logql/)
- [Runbook Template](https://www.runbooktemplate.com/)

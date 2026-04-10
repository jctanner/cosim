#!/usr/bin/env python3
"""
Audit Trail Reconciliation Script (TK-CAF42C)

Recovers missing audit records from Loki structured logs when PostgreSQL writes fail.
Designed to run manually after incidents or via cron for continuous reconciliation.

Usage:
  # Reconcile last 24 hours
  python reconcile_audit_trail.py --since 24h

  # Reconcile specific time range
  python reconcile_audit_trail.py --start "2026-03-28T00:00:00Z" --end "2026-03-28T23:59:59Z"

  # Dry run (no database writes)
  python reconcile_audit_trail.py --since 24h --dry-run

  # Reconcile specific customer
  python reconcile_audit_trail.py --since 24h --customer-id CUST-001

Requirements:
  - Loki endpoint configured in settings
  - PostgreSQL write access to llm_requests table
  - Structured logs include 'event=audit_write_failure' with full audit_data
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
from sqlalchemy import create_engine, and_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert

# Import app models and config
sys.path.insert(0, '/app')
from app.config import settings
from app.models.llm_request import LLMRequest
from app.database import Base


def parse_time_range(args) -> tuple[datetime, datetime]:
    """Parse time range from CLI args"""
    if args.since:
        # Parse relative time (e.g., "24h", "7d")
        unit = args.since[-1]
        value = int(args.since[:-1])

        if unit == 'h':
            delta = timedelta(hours=value)
        elif unit == 'd':
            delta = timedelta(days=value)
        elif unit == 'm':
            delta = timedelta(minutes=value)
        else:
            raise ValueError(f"Invalid time unit: {unit}. Use h/d/m")

        end_time = datetime.utcnow()
        start_time = end_time - delta
    else:
        # Parse absolute timestamps
        start_time = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(args.end.replace('Z', '+00:00'))

    return start_time, end_time


def query_loki_audit_failures(
    start_time: datetime,
    end_time: datetime,
    customer_id: Optional[str] = None
) -> List[Dict]:
    """
    Query Loki for audit_write_failure events with embedded audit_data.

    Returns list of audit records that failed to write to PostgreSQL.
    """
    # Build LogQL query
    label_filters = '{event="audit_write_failure"}'
    if customer_id:
        label_filters = f'{{event="audit_write_failure", customer_id="{customer_id}"}}'

    query = f'{label_filters} | json | line_format "{{{{.audit_data}}}}"'

    # Query Loki API
    params = {
        'query': query,
        'start': int(start_time.timestamp() * 1e9),  # nanoseconds
        'end': int(end_time.timestamp() * 1e9),
        'limit': 10000
    }

    response = requests.get(
        f"{settings.loki_url}/loki/api/v1/query_range",
        params=params,
        timeout=30
    )
    response.raise_for_status()

    result = response.json()

    # Extract audit_data from log lines
    audit_records = []
    for stream in result.get('data', {}).get('result', []):
        for value in stream.get('values', []):
            # value is [timestamp_ns, log_line]
            timestamp_ns, log_line = value

            try:
                # Parse the embedded JSON audit_data
                audit_data = json.loads(log_line)
                audit_records.append(audit_data)
            except json.JSONDecodeError as e:
                print(f"Warning: Failed to parse audit_data from log: {e}", file=sys.stderr)
                continue

    return audit_records


def check_record_exists(session, customer_id: str, timestamp: datetime) -> bool:
    """Check if audit record already exists in PostgreSQL"""
    existing = session.query(LLMRequest).filter(
        and_(
            LLMRequest.customer_id == customer_id,
            LLMRequest.timestamp == timestamp
        )
    ).first()

    return existing is not None


def insert_audit_record(session, audit_data: Dict, dry_run: bool = False) -> bool:
    """
    Insert missing audit record into PostgreSQL.

    Uses INSERT ... ON CONFLICT DO NOTHING to avoid duplicates.
    Returns True if record was inserted, False if it already existed.
    """
    try:
        # Build LLMRequest from audit_data
        record_data = {
            'customer_id': audit_data['customer_id'],
            'timestamp': datetime.fromisoformat(audit_data['timestamp'].replace('Z', '+00:00')),
            'model': audit_data['model'],
            'endpoint': audit_data['endpoint'],
            'tokens_input': audit_data['tokens_input'],
            'tokens_output': audit_data['tokens_output'],
            'cost_input_usd': float(audit_data['cost_input_usd']),
            'cost_output_usd': float(audit_data['cost_output_usd']),
            'pricing_version': audit_data['pricing_version'],
            'response_status': audit_data['response_status'],
            'response_latency_ms': audit_data['response_latency_ms'],
            'throttled': audit_data['throttled'],
            'throttle_reason': audit_data.get('throttle_reason'),
            'rate_limit_remaining': audit_data.get('rate_limit_remaining'),
            'rate_limit_reset_at': audit_data.get('rate_limit_reset_at'),
            'budget_spent_usd': audit_data.get('budget_spent_usd'),
            'budget_limit_usd': audit_data.get('budget_limit_usd')
        }

        if dry_run:
            print(f"  [DRY RUN] Would insert: {record_data['customer_id']} @ {record_data['timestamp']}")
            return True

        # Use INSERT ... ON CONFLICT DO NOTHING for idempotency
        stmt = insert(LLMRequest).values(**record_data)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=['customer_id', 'timestamp']
        )

        result = session.execute(stmt)
        session.commit()

        # Check if row was inserted (rowcount > 0) or already existed (rowcount = 0)
        return result.rowcount > 0

    except Exception as e:
        session.rollback()
        print(f"  ERROR inserting record: {e}", file=sys.stderr)
        return False


def reconcile(
    start_time: datetime,
    end_time: datetime,
    customer_id: Optional[str] = None,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Main reconciliation logic.

    Returns stats: {recovered, already_existed, failed}
    """
    print(f"Reconciling audit trail from {start_time} to {end_time}")
    if customer_id:
        print(f"  Filtering to customer: {customer_id}")
    if dry_run:
        print("  DRY RUN mode - no database writes will occur")

    # Step 1: Query Loki for failed audit writes
    print("\nQuerying Loki for audit_write_failure events...")
    audit_records = query_loki_audit_failures(start_time, end_time, customer_id)
    print(f"Found {len(audit_records)} failed audit writes in Loki logs")

    if not audit_records:
        print("No missing records to reconcile")
        return {'recovered': 0, 'already_existed': 0, 'failed': 0}

    # Step 2: Connect to PostgreSQL
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Step 3: Insert missing records
    stats = {'recovered': 0, 'already_existed': 0, 'failed': 0}

    print(f"\nReconciling {len(audit_records)} records...")
    for i, audit_data in enumerate(audit_records, 1):
        customer = audit_data['customer_id']
        timestamp = audit_data['timestamp']

        # Check if record already exists
        if check_record_exists(session, customer, timestamp):
            stats['already_existed'] += 1
            if i % 100 == 0:
                print(f"  Progress: {i}/{len(audit_records)} (skipped {stats['already_existed']} existing)")
            continue

        # Insert the missing record
        if insert_audit_record(session, audit_data, dry_run):
            stats['recovered'] += 1
            print(f"  ✓ Recovered: {customer} @ {timestamp}")
        else:
            stats['failed'] += 1

    session.close()

    # Step 4: Report results
    print(f"\nReconciliation complete:")
    print(f"  Recovered:       {stats['recovered']}")
    print(f"  Already existed: {stats['already_existed']}")
    print(f"  Failed:          {stats['failed']}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile missing audit records from Loki structured logs"
    )

    # Time range options
    time_group = parser.add_mutually_exclusive_group(required=True)
    time_group.add_argument(
        '--since',
        help='Relative time window (e.g., "24h", "7d", "30m")'
    )
    time_group.add_argument(
        '--start',
        help='Start time (ISO format, e.g., "2026-03-28T00:00:00Z")'
    )

    parser.add_argument(
        '--end',
        help='End time (ISO format). Required with --start'
    )

    # Filters
    parser.add_argument(
        '--customer-id',
        help='Only reconcile records for specific customer'
    )

    # Options
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be recovered without writing to database'
    )

    args = parser.parse_args()

    # Validate args
    if args.start and not args.end:
        parser.error("--end is required when using --start")

    # Parse time range
    try:
        start_time, end_time = parse_time_range(args)
    except ValueError as e:
        parser.error(str(e))

    # Run reconciliation
    try:
        stats = reconcile(
            start_time=start_time,
            end_time=end_time,
            customer_id=args.customer_id,
            dry_run=args.dry_run
        )

        # Exit with error code if any records failed to recover
        sys.exit(1 if stats['failed'] > 0 else 0)

    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()

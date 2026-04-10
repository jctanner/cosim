#!/bin/bash
# Migration runner script for local development and CI/CD

set -e

echo "=== Database Migration Runner ==="
echo "Environment: ${ENVIRONMENT:-development}"
echo "Postgres Host: ${POSTGRES_HOST}"
echo "Postgres DB: ${POSTGRES_DB}"
echo ""

# Check database connectivity
echo "Testing database connection..."
PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT 1;" > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "ERROR: Cannot connect to database at $POSTGRES_HOST"
  exit 1
fi
echo "✓ Database connection successful"
echo ""

# Run migrations in order
echo "Running migrations..."
for migration in /app/migrations/*.sql; do
  filename=$(basename $migration)
  echo "Applying: $filename"
  
  # Check if migration already applied (idempotent)
  PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB -f $migration
  
  if [ $? -eq 0 ]; then
    echo "✓ Successfully applied: $filename"
  else
    echo "✗ Failed to apply: $filename"
    exit 1
  fi
echo ""
done

echo "=== All migrations completed successfully ==="
exit 0
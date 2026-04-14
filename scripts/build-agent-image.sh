#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-agent-image:latest}"

echo "Building agent container image: ${IMAGE}"

# Check prerequisites
if ! command -v podman &>/dev/null; then
    echo "Error: podman not found. Install it first." >&2
    exit 1
fi

if [ ! -f agent-hooks.json ]; then
    echo "Error: agent-hooks.json not found in project root." >&2
    exit 1
fi

# No credential copy needed — credentials are mounted at runtime

echo "Prerequisites OK. Building image..."
podman build -f Dockerfile.agent -t "$IMAGE" .
echo "Done: $IMAGE"

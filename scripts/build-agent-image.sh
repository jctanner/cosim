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

if [ ! -f container/agent-hooks.json ]; then
    echo "Error: container/agent-hooks.json not found." >&2
    exit 1
fi

echo "Prerequisites OK. Building image..."
podman build -f container/Dockerfile.agent -t "$IMAGE" container/
echo "Done: $IMAGE"

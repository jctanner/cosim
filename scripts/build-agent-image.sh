#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CREDS="application_default_credentials.json"
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

if [ ! -f "$CREDS" ]; then
    SRC="$HOME/.config/gcloud/application_default_credentials.json"
    if [ -f "$SRC" ]; then
        echo "Copying GCP credentials from $SRC"
        cp "$SRC" "$CREDS"
    else
        echo "Error: GCP credentials not found." >&2
        echo "  Run: gcloud auth application-default login" >&2
        echo "  Then: cp ~/.config/gcloud/application_default_credentials.json ." >&2
        exit 1
    fi
fi

echo "Prerequisites OK. Building image..."
podman build -f Dockerfile.agent -t "$IMAGE" .
echo "Done: $IMAGE"

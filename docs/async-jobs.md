# Async Job Execution — Design Plan

## Problem

Agents (especially Sam, the Prototype Engineer) write code and then hallucinate its output because they have no way to actually execute anything. We need a mechanism for agents to run real commands on a remote VM, retrieve results, and share them with the team — without blocking their turn or hitting the 5-minute `container_timeout`.

## Design

### Core Idea

Podman containers are used as throwaway SSH clients. An agent calls `run_command("python3 backtest.py")`, the MCP server launches a detached podman container that SSH's into a VM and runs the command, then returns the container ID as a job ID. A monitor thread watches for completion via `podman wait`, then sends a DM to the agent. On the next orchestrator poll cycle, the DM triggers the agent to wake up, call `get_job(job_id)`, and retrieve real output.

### Architecture

```
Agent Container              MCP Server (5001)              Podman Job Container          VM (SSH)
  │                            │                              │                            │
  ├─ run_command(cmd) ────────►│                              │                            │
  │                            ├─ podman run --detach ───────►│                            │
  │                            │   ssh-job-image              ├─ ssh user@vm "cmd" ───────►│
  │◄── {job_id: cid} ─────────┤                              │                            ├─ runs command
  │                            │                              │                            │
  ├─ signal_done() ───────────►│                              │                            │
  │                            │                              │                            │
  │   ... agent turn ends ...  │                              │◄─── stdout + exit code ────┤
  │                            │                              │ (container exits)           │
  │                            ├─ podman wait cid ────────────┤                            │
  │                            ├─ podman logs cid ────────────┤                            │
  │                            ├─ send_dm(agent, "done") ─────┤                            │
  │                            │                              │                            │
  │   ... orchestrator polls, sees DM, triggers agent ...     │                            │
  │                            │                              │                            │
  ├─ get_job(job_id) ─────────►│                              │                            │
  │                            ├─ podman logs cid ────────────┤                            │
  │◄── {stdout, exit_code} ────┤                              │                            │
```

## Components

### 1. SSH Job Container Image (`Dockerfile.ssh-job`)

Minimal image with an SSH client. No application code — it just connects to the VM and runs whatever command it's given.

```dockerfile
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest
RUN microdnf install -y openssh-clients coreutils-single && microdnf clean all
# Configure SSH for non-interactive use
RUN mkdir -p /root/.ssh && \
    printf "Host *\n  StrictHostKeyChecking accept-new\n  UserKnownHostsFile /root/.ssh/known_hosts\n  ServerAliveInterval 30\n  ServerAliveCountMax 3\n  ConnectTimeout 10\n  BatchMode yes\n  LogLevel ERROR\n" \
    > /root/.ssh/config && \
    chmod 700 /root/.ssh && chmod 600 /root/.ssh/config
# Use timeout wrapper as entrypoint so jobs can't run forever
# The actual timeout value is passed as the first arg at runtime
ENTRYPOINT ["timeout"]
```

**Note on `BatchMode yes`**: This is critical for non-interactive SSH. Without it, a container could hang forever waiting for a password prompt if key auth fails.

**Note on `timeout` entrypoint**: Podman has no native `--timeout` flag for `podman run`. Instead, the container uses the `timeout` coreutil as its entrypoint. The MCP server passes the timeout value as the first argument, followed by the SSH command. This sends SIGTERM to the SSH process when the timeout expires.

SSH keys are mounted at runtime via `-v`, not baked into the image:

```bash
podman build -f Dockerfile.ssh-job -t ssh-job-image:latest .
```

### 2. Job Tracker (`lib/jobs.py`)

New module — ~80 lines. Manages job state in-memory and monitors completion in background threads.

```python
import hashlib
import subprocess
import threading
import time

import requests

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

SSH_KEY_PATH = ""      # set at startup
SSH_USER = ""           # set at startup
SSH_HOST = ""           # set at startup
FLASK_URL = ""          # set at startup
JOB_IMAGE = "ssh-job-image:latest"
JOB_TIMEOUT = 600      # seconds


def configure(ssh_key_path: str, ssh_user: str, ssh_host: str,
              flask_url: str, job_image: str = JOB_IMAGE,
              job_timeout: int = JOB_TIMEOUT):
    global SSH_KEY_PATH, SSH_USER, SSH_HOST, FLASK_URL, JOB_IMAGE, JOB_TIMEOUT
    SSH_KEY_PATH = ssh_key_path
    SSH_USER = ssh_user
    SSH_HOST = ssh_host
    FLASK_URL = flask_url
    JOB_IMAGE = job_image
    JOB_TIMEOUT = job_timeout


def _generate_job_id() -> str:
    return "JOB-" + hashlib.sha256(
        f"{time.time()}-{id(threading.current_thread())}".encode()
    ).hexdigest()[:6]


def submit_job(agent_key: str, display_name: str, command: str) -> dict:
    """Launch a detached podman container that SSH's to the VM and runs command."""
    job_id = _generate_job_id()

    # The entrypoint is "timeout", so args are: <seconds> ssh <user@host> <command>
    # This ensures the SSH session is killed if it exceeds JOB_TIMEOUT.
    # Note: --rm with --detach is broken in rootless podman, so we clean up
    # explicitly in _monitor_job after collecting logs.
    result = subprocess.run(
        [
            "podman", "run", "--detach",
            "--label", f"job-id={job_id}",
            "--label", f"job-agent={agent_key}",
            "--name", f"job-{job_id.lower()}",
            "-v", f"{SSH_KEY_PATH}:/root/.ssh/id_rsa:ro,Z",
            JOB_IMAGE,
            str(JOB_TIMEOUT), "ssh",
            f"{SSH_USER}@{SSH_HOST}", command,
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        return {"error": result.stderr.strip(), "job_id": job_id}

    container_id = result.stdout.strip()

    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "container_id": container_id,
            "agent_key": agent_key,
            "display_name": display_name,
            "command": command,
            "status": "running",
            "submitted_at": time.time(),
            "completed_at": None,
            "exit_code": None,
            "stdout": None,
            "stderr": None,
        }

    # Start monitor thread
    t = threading.Thread(target=_monitor_job, args=(job_id,), daemon=True)
    t.start()

    return {"job_id": job_id, "status": "running"}


def _monitor_job(job_id: str):
    """Block on podman wait, then collect logs and send a DM."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return

    cid = job["container_id"]

    # podman wait blocks until the container exits
    wait_result = subprocess.run(
        ["podman", "wait", cid],
        capture_output=True, text=True, timeout=JOB_TIMEOUT + 60,
    )
    exit_code = int(wait_result.stdout.strip()) if wait_result.stdout.strip().lstrip("-").isdigit() else -1

    # Collect logs
    logs_result = subprocess.run(
        ["podman", "logs", "--tail", "500", cid],
        capture_output=True, text=True, timeout=30,
    )

    with _jobs_lock:
        job["status"] = "done"
        job["exit_code"] = exit_code
        job["stdout"] = logs_result.stdout
        job["stderr"] = logs_result.stderr
        job["completed_at"] = time.time()

    # Send DM to the agent via Flask API
    elapsed = job["completed_at"] - job["submitted_at"]
    status_word = "completed" if exit_code == 0 else f"failed (exit {exit_code})"
    dm_content = (
        f"[DM to {job['agent_key']}] Job {job_id} {status_word} "
        f"after {elapsed:.0f}s. Use get_job('{job_id}') to retrieve output."
    )
    try:
        requests.post(
            f"{FLASK_URL}/api/messages",
            json={"sender": "System", "content": dm_content, "channel": "#dms"},
            timeout=10,
        )
    except Exception:
        pass  # best-effort

    # Clean up the container
    subprocess.run(["podman", "rm", "-f", cid], capture_output=True, timeout=10)


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        return _jobs.get(job_id, {}).copy() or None


def list_jobs(agent_key: str | None = None) -> list[dict]:
    with _jobs_lock:
        jobs = list(_jobs.values())
    if agent_key:
        jobs = [j for j in jobs if j["agent_key"] == agent_key]
    # Return without full stdout/stderr for listing
    return [
        {k: v for k, v in j.items() if k not in ("stdout", "stderr")}
        for j in jobs
    ]


def get_jobs_snapshot() -> dict:
    """For session save."""
    with _jobs_lock:
        return {k: v.copy() for k, v in _jobs.items()}


def restore_jobs(data: dict):
    """For session load. Running jobs are marked stale."""
    with _jobs_lock:
        _jobs.clear()
        for k, v in data.items():
            if v.get("status") == "running":
                v["status"] = "stale"
            _jobs[k] = v


def clear_jobs():
    """For session new."""
    with _jobs_lock:
        _jobs.clear()
```

### 3. MCP Tools (`lib/mcp_server.py`)

Three new tools, registered alongside existing tools. Only agents with `enable_jobs: true` in scenario settings get access.

```python
@server.tool(
    name="run_command",
    description="Run a command on the remote execution VM. Returns a job ID immediately. "
                "Use get_job() to retrieve results after the job completes. "
                "You will receive a DM when the job finishes.",
)
async def run_command(command: str) -> str:
    from lib.jobs import submit_job
    result = submit_job(agent_key, display_name, command)
    _record_audit(agent_key, "run_command", {"command": command},
                  result.get("job_id", ""), 0)
    return json.dumps(result)

@server.tool(
    name="get_job",
    description="Get the status and output of a previously submitted job.",
)
async def get_job(job_id: str) -> str:
    from lib.jobs import get_job as _get_job
    result = _get_job(job_id)
    if result is None:
        return json.dumps({"error": f"job {job_id} not found"})
    # Truncate large outputs
    if result.get("stdout") and len(result["stdout"]) > 10000:
        result["stdout"] = result["stdout"][:10000] + "\n... (truncated)"
    _record_audit(agent_key, "get_job", {"job_id": job_id},
                  result.get("status", ""), 0)
    return json.dumps(result)

@server.tool(
    name="list_jobs",
    description="List your submitted jobs with status.",
)
async def list_jobs() -> str:
    from lib.jobs import list_jobs as _list_jobs
    result = _list_jobs(agent_key=agent_key)
    _record_audit(agent_key, "list_jobs", {}, f"{len(result)} jobs", 0)
    return json.dumps(result)
```

### 4. DM-Based Agent Triggering (`lib/container_orchestrator.py`)

The orchestrator's main poll loop currently only triggers on human messages. Add a check for pending DMs that should trigger agent turns.

In the main loop (around line 1265), after checking for human messages:

```python
# Existing: check for human messages
new_messages = client.get_messages(since=last_seen_id)
human_messages = [m for m in new_messages if not _is_agent_message(m)]

# NEW: check for job-completion DMs that should trigger agents
if not human_messages:
    dm_messages = [
        m for m in new_messages
        if m.get("channel") == "#dms"
        and m.get("sender") == "System"
        and "Job JOB-" in m.get("content", "")
    ]
    if dm_messages:
        # Extract agent keys from DM content and trigger via director channels
        for dm in dm_messages:
            content = dm.get("content", "")
            # Parse "[DM to agentkey]" prefix
            if "[DM to " in content:
                agent_key = content.split("[DM to ")[1].split("]")[0]
                trigger_channels.add(f"#director-{agent_key}")
```

This reuses the existing `#director-*` channel mechanism — it's already designed to trigger specific agents without triggering everyone else.

### 5. Scenario Configuration

Add job settings to `scenario.yaml`:

```yaml
settings:
  enable_jobs: true
  job_image: ssh-job-image:latest
  job_timeout: 600
  job_vm:
    host: 192.168.1.100
    user: agent
    ssh_key: /path/to/id_rsa
  job_agents:         # which agents get the job tools
    - prototype
    - technical
```

### 6. Session Management (`lib/session.py`)

Add job state to save/load:

- **Save**: `jobs.get_jobs_snapshot()` → `var/instances/{name}/jobs.json`
- **Load**: `jobs.restore_jobs(data)` — running jobs marked `stale` since their containers are gone
- **New**: `jobs.clear_jobs()`

### 7. Web UI — Job Monitor

Add a small panel (or integrate into existing UI) showing active and completed jobs:

- Job ID, agent, command, status, duration
- Expandable stdout/stderr viewer
- Auto-refresh via SSE

REST endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/jobs` | GET | List all jobs (optional `?agent=key` filter) |
| `/api/jobs/<job_id>` | GET | Get job detail with stdout/stderr |

### 8. Build Script (`scripts/build-ssh-job-image.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-ssh-job-image:latest}"
echo "Building SSH job container image: ${IMAGE}"

if ! command -v podman &>/dev/null; then
    echo "Error: podman not found." >&2
    exit 1
fi

podman build -f Dockerfile.ssh-job -t "$IMAGE" .
echo "Done: $IMAGE"
```

## Agent Workflow Example

1. Dr. Chen assigns Sam a task: "Write a Python script that benchmarks HTTP request latency to our API endpoint."

2. Sam writes the script and commits it to mock GitLab:
   ```
   commit_files("benchmarks", [{"path": "latency_test.py", "content": "..."}], "Add latency benchmark")
   ```

3. Sam runs the script on the VM:
   ```
   run_command("python3 -c '...script content...'")
   → {"job_id": "JOB-a3f2c1", "status": "running"}
   ```

4. Sam calls `signal_done()` — his turn ends.

5. 30 seconds later, the job finishes. The monitor thread sends a DM:
   `[DM to prototype] Job JOB-a3f2c1 completed after 28s. Use get_job('JOB-a3f2c1') to retrieve output.`

6. Orchestrator sees the DM on next poll, triggers Sam via `#director-prototype`.

7. Sam reads the DM, calls `get_job("JOB-a3f2c1")`, gets real stdout with actual latency numbers.

8. Sam posts findings to `#technical` with real data — no hallucination.

## Process Startup

```bash
# Terminal 1 — Flask server
python main.py server --port 5000 --scenario research-lab

# Terminal 2 — MCP server
python main.py mcp-server --port 5001 --scenario research-lab

# Terminal 3 — Container orchestrator
python main.py chat --model opus --scenario research-lab \
  --done-timeout 360 --container-timeout 600
```

The SSH job image must be pre-built:
```bash
scripts/build-ssh-job-image.sh
```

The VM must be accessible via SSH from the host running the MCP server.

## Podman Implementation Details

### Starting a Job

```bash
podman run --detach \
  --label job-id=JOB-a3f2c1 \
  --label job-agent=prototype \
  --name job-job-a3f2c1 \
  -v /path/to/id_rsa:/root/.ssh/id_rsa:ro,Z \
  ssh-job-image:latest \
  600 ssh agent@192.168.1.100 "python3 benchmark.py"
```

Returns the container ID on stdout. The `timeout 600` entrypoint kills the SSH process after 600s if it hangs. Labels enable filtering with `podman ps --filter label=job-agent=prototype`.

### Waiting for Completion

```bash
podman wait <container-id>
```

Blocks until the container exits. Prints the exit code to stdout. The monitor thread calls this in a background thread, so it doesn't block the MCP server.

### Retrieving Output

```bash
podman logs --tail 500 <container-id>
```

Returns stdout from the SSH session (which is the command's stdout). The `--tail 500` limits output to prevent memory issues with verbose commands.

### Cleanup

```bash
podman rm -f <container-id>
```

Called after logs are captured. `--rm` with `--detach` is broken in rootless podman ([#13860](https://github.com/containers/podman/issues/13860)), so explicit cleanup is required.

### Listing Active Jobs

```bash
podman ps --filter label=job-agent=prototype --format "{{.ID}} {{.Labels}} {{.Status}}"
```

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `Dockerfile.ssh-job` | Create | Minimal SSH client image |
| `scripts/build-ssh-job-image.sh` | Create | Build script for job image |
| `lib/jobs.py` | Create | Job tracker module |
| `lib/mcp_server.py` | Modify | Add `run_command`, `get_job`, `list_jobs` tools |
| `lib/container_orchestrator.py` | Modify | Add DM-based agent triggering in poll loop |
| `lib/session.py` | Modify | Add job state to save/load/new |
| `lib/webapp/routes/jobs.py` | Create | REST endpoints for job monitoring |
| `lib/webapp/template.py` | Modify | Add job monitor panel to web UI |
| `scenarios/research-lab/scenario.yaml` | Modify | Add job settings |

## Open Questions

1. **File transfer** — Agents can inline short scripts in the command (`python3 -c '...'`), but for multi-file projects they'd need `scp` or similar. A future `upload_file(path, content)` MCP tool could write files to the VM before execution.

2. **Persistent VM state** — Should the VM be wiped between sessions, or do agents accumulate tools/data across sessions? For the red-team scenario, ephemeral VMs (snapshot/restore) are safer. For research, persistence is more useful.

3. **Multiple VMs** — The current design assumes one VM. For the red-team scenario, agents might want a target VM and an attacker VM. The `run_command` tool could accept an optional `target` parameter.

4. **Output size** — Large outputs (build logs, data dumps) could blow up agent context windows. The `get_job` tool truncates at 10KB. Should there be a `get_job_output(job_id, offset, limit)` for pagination?

5. **Concurrent job limits** — Should there be a cap on how many jobs an agent can have running simultaneously? Probably yes — 3-5 per agent.

6. **Mock GitLab integration** — Currently, code committed to the mock GitLab lives only in Flask's memory. Agents can't `git clone` from the VM. Possible solutions: (a) agent inlines code in the command, (b) `upload_file` MCP tool, (c) make mock GitLab serve real git repos. This is a separate effort — see the mock GitLab subsystem for details.

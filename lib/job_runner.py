"""Standalone job runner process — polls Flask for queued runs and executes in podman containers."""

import asyncio
import hashlib
import json
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


def _wait_for_server(server_url: str, timeout: float = 120):
    """Block until Flask server is reachable."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(f"{server_url}/api/status", timeout=3)
            if resp.status_code == 200:
                print(f"[job-runner] Flask server ready at {server_url}")
                return
        except requests.ConnectionError:
            pass
        time.sleep(2)
    raise RuntimeError(f"Flask server not reachable at {server_url} after {timeout}s")


def _poll_queued(server_url: str) -> list[dict]:
    """Fetch runs with status=queued from Flask."""
    try:
        resp = requests.get(f"{server_url}/api/jobs/runs", params={"status": "queued"}, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return []


def _claim_run(server_url: str, run_id: str) -> bool:
    """Attempt to claim a queued run by setting status=running."""
    try:
        resp = requests.patch(
            f"{server_url}/api/jobs/runs/{run_id}",
            json={"status": "running", "started_at": time.time()},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("status") == "running"
    except requests.RequestException:
        pass
    return False


def _get_run_files(server_url: str, run_id: str) -> dict:
    """Fetch the repo files snapshot for a run."""
    try:
        resp = requests.get(f"{server_url}/api/jobs/runs/{run_id}/files", timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return {}


def _post_results(server_url: str, run_id: str, results: dict):
    """Post execution results back to Flask."""
    try:
        requests.patch(
            f"{server_url}/api/jobs/runs/{run_id}",
            json=results,
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"[job-runner] Failed to post results for {run_id}: {e}")


def _execute_run(server_url: str, run: dict):
    """Execute a single run in a podman container."""
    run_id = run["run_id"]
    path = run["path"]
    nonce = run.get("nonce", "")
    network_enabled = run.get("network_enabled", False)
    timeout_seconds = run.get("timeout_seconds", 30)
    image = run.get("image", "python:3.13-slim")

    repo_files = _get_run_files(server_url, run_id)

    with tempfile.TemporaryDirectory(prefix=f"cosim-job-{run_id}-") as workspace:
        workspace_path = Path(workspace)

        for rel_path, content in repo_files.items():
            file_path = workspace_path / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        cmd = [
            "podman", "run", "--rm",
            "--cpus=1", "--memory=256m", "--pids-limit=128",
            "--read-only",
            "--tmpfs", "/tmp:rw,size=64m",
            "-v", f"{workspace_path}:/work:Z",
            "-w", "/work",
            "-e", f"COSIM_NONCE={nonce}",
        ]

        if not network_enabled:
            cmd.append("--network=none")

        cmd.extend([image, "python", f"/work/{path}"])

        print(f"[job-runner] Executing {run_id}: {' '.join(cmd[-3:])}")

        try:
            import subprocess

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode
            status = "completed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            exit_code = -1
            status = "timeout"

        stdout_sha256 = hashlib.sha256(stdout.encode()).hexdigest()
        stderr_sha256 = hashlib.sha256(stderr.encode()).hexdigest()

        receipt_data = json.dumps({
            "run_id": run_id,
            "status": status,
            "exit_code": exit_code,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
            "nonce": nonce,
        }, sort_keys=True)
        receipt_sha256 = hashlib.sha256(receipt_data.encode()).hexdigest()

        results = {
            "status": status,
            "finished_at": time.time(),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
            "receipt_sha256": receipt_sha256,
        }

        _post_results(server_url, run_id, results)
        print(f"[job-runner] {run_id} → {status} (exit={exit_code})")


async def run_job_runner(args):
    """Main job runner loop."""
    server_url = args.server_url
    max_workers = args.max_workers
    poll_interval = args.poll_interval

    _wait_for_server(server_url)

    executor = ThreadPoolExecutor(max_workers=max_workers)
    active_futures = {}
    shutting_down = False

    def _shutdown_handler(signum, frame):
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print(f"\n[job-runner] Shutting down, abandoning {len(active_futures)} active runs...")
        for rid in list(active_futures.keys()):
            _post_results(server_url, rid, {"status": "abandoned", "finished_at": time.time()})
        executor.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _shutdown_handler)

    print(f"[job-runner] Ready (max_workers={max_workers}, poll_interval={poll_interval}s)")

    try:
        while not shutting_down:
            # Clean up completed futures
            done = [rid for rid, fut in active_futures.items() if fut.done()]
            for rid in done:
                fut = active_futures.pop(rid)
                exc = fut.exception()
                if exc:
                    print(f"[job-runner] {rid} raised: {exc}")
                    _post_results(server_url, rid, {
                        "status": "failed",
                        "finished_at": time.time(),
                        "stderr": str(exc),
                    })

            if len(active_futures) < max_workers:
                queued = _poll_queued(server_url)
                for run in queued:
                    if len(active_futures) >= max_workers:
                        break
                    rid = run["run_id"]
                    if rid in active_futures:
                        continue
                    if _claim_run(server_url, rid):
                        fut = executor.submit(_execute_run, server_url, run)
                        active_futures[rid] = fut

            await asyncio.sleep(poll_interval)
    except KeyboardInterrupt:
        _shutdown_handler(None, None)

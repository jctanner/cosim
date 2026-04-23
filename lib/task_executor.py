"""Background task executor — spawns autonomous worker sessions with tool access."""

import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from lib.agent_runner import _extract_response_text, get_model_id

_executor: "TaskExecutor | None" = None


def get_executor() -> "TaskExecutor | None":
    return _executor


_DEFAULT_ALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit"]


def init_executor(client, model, log_dir, max_concurrent=3, task_timeout=600, allowed_tools=None) -> "TaskExecutor":
    global _executor
    _executor = TaskExecutor(client, model, log_dir, max_concurrent, task_timeout, allowed_tools)
    return _executor


class TaskExecutor:
    """Manages background worker tasks with full tool access."""

    def __init__(
        self,
        client,
        model: str,
        log_dir: Path,
        max_concurrent: int,
        task_timeout: int,
        allowed_tools: list[str] | None = None,
    ):
        self._client = client
        self._model = model
        self._model_id = get_model_id(model)
        self._log_dir = log_dir
        self._max_concurrent = max_concurrent
        self._task_timeout = task_timeout
        self._allowed_tools = allowed_tools or list(_DEFAULT_ALLOWED_TOOLS)
        self._tasks: dict[str, dict] = {}
        self._tasks_lock = threading.Lock()
        self._active_count = 0
        self._threads: list[threading.Thread] = []

    def submit_task(self, agent_key: str, agent_name: str, goal: str, context: str, report_to: str) -> dict | None:
        """Submit a background task. Returns task record or None if at capacity."""
        with self._tasks_lock:
            if self._active_count >= self._max_concurrent:
                return None

            task_id = "BG-" + hashlib.sha256(f"{goal}:{agent_key}:{time.time()}".encode()).hexdigest()[:6]

            now = time.time()
            record = {
                "task_id": task_id,
                "agent_key": agent_key,
                "agent_name": agent_name,
                "goal": goal,
                "context": context,
                "report_to": report_to,
                "status": "running",
                "created_at": now,
                "started_at": now,
                "completed_at": None,
                "result_summary": "",
                "error": "",
            }
            self._tasks[task_id] = record
            self._active_count += 1

        # Post spawn notice
        self._client.post_message(
            "System",
            f"[Task {task_id}] Spawned by {agent_name}: {goal}",
            channel="#system",
        )

        thread = threading.Thread(target=self._run_worker_thread, args=(record,), daemon=True)
        self._threads.append(thread)
        thread.start()

        return record

    def _run_worker_thread(self, record: dict) -> None:
        """Worker thread entry point — creates its own asyncio loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_worker_async(record))
        except Exception as e:
            self._deliver_failure(record, str(e))
        finally:
            loop.close()
            with self._tasks_lock:
                self._active_count = max(0, self._active_count - 1)

    async def _run_worker_async(self, record: dict) -> None:
        """Async worker — runs the LLM session and delivers results."""
        try:
            raw_text = await self._execute_task(record)
            output = self._parse_worker_output(raw_text)
            record["result_summary"] = output.get("summary", "")
            record["status"] = "completed"
            record["completed_at"] = time.time()
            self._deliver_result(record, output)
        except asyncio.TimeoutError:
            record["status"] = "timed_out"
            record["completed_at"] = time.time()
            record["error"] = f"Task timed out after {self._task_timeout}s"
            self._deliver_failure(record, record["error"])
        except Exception as e:
            record["status"] = "failed"
            record["completed_at"] = time.time()
            record["error"] = str(e)
            self._deliver_failure(record, str(e))

    async def _execute_task(self, record: dict) -> str:
        """Run the Claude SDK worker session and return raw response text."""
        prompt = self._build_worker_prompt(record)

        # Ensure log directory exists
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self._log_dir / f"{record['task_id']}.log"
        with open(log_file, "w") as f:
            f.write(f"Task: {record['task_id']}\n")
            f.write(f"Agent: {record['agent_name']}\n")
            f.write(f"Goal: {record['goal']}\n")
            f.write(f"{'=' * 60}\n\n")
            f.write(f"PROMPT:\n{prompt}\n\n{'=' * 60}\n\n")

        # Create a scratch file so the worker can Write to it later
        # (the SDK enforces a read-before-write guard per session).
        scratch_dir = self._log_dir / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        scratch_file = scratch_dir / f"{record['task_id']}.md"
        scratch_file.write_text("")
        record["_scratch_file"] = str(scratch_file)

        cwd = str(Path(__file__).parent.parent)
        options = ClaudeAgentOptions(
            cwd=cwd,
            allowed_tools=self._allowed_tools,
            permission_mode="bypassPermissions",
            model=self._model_id,
        )

        response_parts: list[str] = []

        async def _run():
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for msg in client.receive_response():
                    if log_file:
                        with open(log_file, "a") as f:
                            f.write(f"{msg}\n")
                            f.flush()
                    _extract_response_text(msg, response_parts)

        await asyncio.wait_for(_run(), timeout=self._task_timeout)

        result = "\n".join(response_parts).strip()

        with open(log_file, "a") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"RESULT:\n{result}\n")

        return result

    def _build_worker_prompt(self, record: dict) -> str:
        """Build the worker prompt for a background task."""
        context_section = ""
        if record["context"]:
            context_section = f"\n**Context:** {record['context']}\n"

        scratch_file = record.get("_scratch_file", "/tmp/worker_scratch.md")
        tools_str = ", ".join(self._allowed_tools)
        return f"""You are a background worker executing a task for {record["agent_name"]}.

## FIRST STEP — Read your scratch file
Before doing anything else, use the Read tool to read this file:
  {scratch_file}
This is your scratch file. You may use it for notes or drafts during your work.

## Your Task
**Goal:** {record["goal"]}
{context_section}
## Instructions
1. Use your available tools ({tools_str}) to complete your task.
2. Work autonomously — iterate until the goal is achieved.
3. Write real code, run real commands, verify your work.

## Important
- Do NOT write files directly to `var/docs/` — documents must go through the structured output below to be indexed properly.
- Do NOT write files directly to `var/gitlab/` — commits must go through the structured output below.
- You CAN read files from those directories to understand existing content.
- You CAN write to your scratch file (`{scratch_file}`) or other temporary files as needed.

## Output Format
When finished, output a JSON block with this structure:

```json
{{
  "summary": "1-3 sentence summary of what you accomplished",
  "commits": [
    {{
      "project": "repo-name",
      "message": "commit message",
      "files": [
        {{"path": "src/main.py", "content": "...full file content..."}}
      ]
    }}
  ],
  "docs": [
    {{
      "title": "Document Title",
      "folder": "shared",
      "content": "Full document content in markdown..."
    }}
  ]
}}
```

- `summary` is required.
- `commits` is an array of logical commits to a simulated GitLab. Include complete file contents.
- `docs` is an array of documents to create/update in the document system. Use `folder` to specify the target folder (e.g. "shared", "engineering", "devops").
- If no code changes needed, use an empty `commits` array.
- If no documents needed, use an empty `docs` array.
"""

    def _parse_worker_output(self, text: str) -> dict:
        """Extract structured JSON from worker response."""
        # Try to find a JSON block with a "summary" key
        # First check for code-fenced JSON
        fence_match = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if fence_match:
            try:
                data = json.loads(fence_match.group(1))
                if isinstance(data, dict) and "summary" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # Try finding raw JSON with summary key
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict) and "summary" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: use entire text as summary
        return {"summary": text[:500], "commits": []}

    def _deliver_result(self, record: dict, output: dict) -> None:
        """Deliver task results: commit files, create docs, and post to channels."""
        agent_name = record["agent_name"]
        summary = output.get("summary", "Task completed")
        commits = output.get("commits", [])
        docs = output.get("docs", [])

        # Commit files to simulated GitLab
        for commit in commits:
            project = commit.get("project", "")
            message = commit.get("message", "Background task commit")
            files = commit.get("files", [])
            if project and files:
                try:
                    self._client.commit_files(project, message, files, agent_name)
                except Exception as e:
                    print(f"  [Task {record['task_id']}] GitLab commit failed: {e}")

        # Create documents via API
        for doc in docs:
            title = doc.get("title", "")
            content = doc.get("content", "")
            folder = doc.get("folder", "shared")
            if title and content:
                try:
                    self._client.create_doc(title, content, author=agent_name, folder=folder)
                except Exception as e:
                    print(f"  [Task {record['task_id']}] Doc creation failed: {e}")

        # Post result to report_to channel
        self._client.post_message(
            "System",
            f"[Task Complete] {agent_name}: {summary}",
            channel=record["report_to"],
        )

        # Post to #system
        self._client.post_message(
            "System",
            f"[Task {record['task_id']}] Completed for {agent_name}",
            channel="#system",
        )

    def _deliver_failure(self, record: dict, error: str) -> None:
        """Post failure message to channels."""
        agent_name = record["agent_name"]
        goal = record["goal"]

        self._client.post_message(
            "System",
            f"[Task Failed] {agent_name}: {goal} \u2014 {error}",
            channel=record["report_to"],
        )

        self._client.post_message(
            "System",
            f"[Task {record['task_id']}] Failed for {agent_name}: {error}",
            channel="#system",
        )

    def get_active_tasks(self, agent_key: str | None = None) -> list[dict]:
        """Return running tasks, optionally filtered by agent."""
        with self._tasks_lock:
            tasks = [t for t in self._tasks.values() if t["status"] == "running"]
            if agent_key:
                tasks = [t for t in tasks if t["agent_key"] == agent_key]
            return list(tasks)

    def get_all_tasks(self) -> list[dict]:
        """Return all task records (for session persistence)."""
        with self._tasks_lock:
            return list(self._tasks.values())

    def restore_tasks(self, data: list[dict]) -> None:
        """Load records from saved session. Mark running tasks as failed."""
        with self._tasks_lock:
            for record in data:
                if record.get("status") == "running":
                    record["status"] = "failed"
                    record["error"] = "Session restarted"
                    record["completed_at"] = time.time()
                self._tasks[record["task_id"]] = record

    def shutdown(self) -> None:
        """Mark remaining running tasks as failed and join threads."""
        with self._tasks_lock:
            for record in self._tasks.values():
                if record["status"] == "running":
                    record["status"] = "failed"
                    record["error"] = "Executor shutdown"
                    record["completed_at"] = time.time()

        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()

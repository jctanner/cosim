"""Shared in-memory state for the webapp package."""

import queue
import re
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

CHAT_LOG = BASE_DIR / "var" / "chat.log"
DOCS_DIR = BASE_DIR / "var" / "docs"
LOGS_DIR = BASE_DIR / "var" / "logs"

# Regexes to parse ResultMessage lines written by agent_runner.
# The usage dict contains nested sub-dicts, so we extract token counts
# directly from the line rather than trying to parse the full dict.
# SDK repr format: ResultMessage(...total_cost_usd=0.123, usage={'input_tokens': 10, ...})
_RESULT_MSG_RE = re.compile(r"ResultMessage\(.*?total_cost_usd=(?P<cost>[0-9eE.+-]+|None)")
# Claude CLI JSON format: {"type":"result","total_cost_usd":0.123,"usage":{"input_tokens":10,...}}
_RESULT_JSON_RE = re.compile(r'"type"\s*:\s*"result".*?"total_cost_usd"\s*:\s*(?P<cost>[0-9eE.+-]+|null)')
# Token patterns match both single-quoted (Python repr) and double-quoted (JSON) keys
_INPUT_TOKENS_RE = re.compile(r"""["']input_tokens["']\s*:\s*(\d+)""")
_OUTPUT_TOKENS_RE = re.compile(r"""["']output_tokens["']\s*:\s*(\d+)""")
_CACHE_CREATE_RE = re.compile(r"""["']cache_creation_input_tokens["']\s*:\s*(\d+)""")
_CACHE_READ_RE = re.compile(r"""["']cache_read_input_tokens["']\s*:\s*(\d+)""")


# In-memory state
_messages: list[dict] = []
_lock = threading.Lock()
_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()

# Channel registry: channel_name -> {description, is_external, created_at}
_channels: dict[str, dict] = {}
# Channel membership: channel_name -> set of persona keys
_channel_members: dict[str, set[str]] = {}
_channel_lock = threading.Lock()

# Document index: slug -> metadata dict
_docs_index: dict[str, dict] = {}
_docs_lock = threading.Lock()

# Folder registry: folder_name -> {type, description}
_folders: dict[str, dict] = {}
# Folder access: folder_name -> set of persona keys
_folder_access: dict[str, set[str]] = {}
_folder_lock = threading.Lock()

# GitLab state: repo_name -> metadata, repo_name -> commit list, repo_name -> MR list
_gitlab_repos: dict[str, dict] = {}
_gitlab_commits: dict[str, list[dict]] = {}
_gitlab_merge_requests: dict[str, list[dict]] = {}
_gitlab_lock = threading.Lock()

# Tickets state: ticket_id -> full ticket dict
_tickets: dict[str, dict] = {}
_tickets_lock = threading.Lock()

# Jobs state: run_id -> full run record dict
_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()

# Recaps: list of generated recaps
_recaps: list[dict] = []

# Agent online/offline state: persona_key -> True (online) / False (offline)
_agent_online: dict[str, bool] = {}
_agent_firing: set[str] = set()  # agents being fired (waiting for session close)
_agent_verbosity: dict[str, str] = {}  # persona_key -> verbosity level
_agent_last_activity: dict[str, dict] = {}  # persona_key -> {timestamp, event_type, detail}
_agent_online_lock = threading.Lock()

# Agent thoughts: persona_key -> list of {thinking, response, timestamp}
_agent_thoughts: dict[str, list[dict]] = {}
_agent_thoughts_lock = threading.Lock()

# Orchestrator status (updated via heartbeat from orchestrator process)
_orchestrator_status: dict = {
    "state": "disconnected",  # disconnected, starting, ready, responding, restarting
    "scenario": None,
    "agents": {},  # persona_key -> {state, display_name}
    "last_heartbeat": 0,
    "message": "",
}
_orchestrator_lock = threading.Lock()

# Control signal queue for orchestrator (checked on each poll)
_orchestrator_commands: list[dict] = []
_command_lock = threading.Lock()

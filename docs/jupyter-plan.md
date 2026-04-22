# JupyterLab Notebook Subsystem — Design Plan

## Overview

Add a notebook execution subsystem that allows agents (especially the research-lab team) to create, edit, and execute Jupyter notebooks for quantitative analysis. A containerized notebook service manages kernels and execution. Agents interact via MCP tools using a fire-and-poll pattern — no persistent connections. Notebooks are stored as `.ipynb` files in `var/notebooks/` and are visible to the human observer in the web UI in real-time.

Primary use case: research agents investigate the feasibility of algorithmic day trading by pulling market data, running statistical analysis, backtesting strategies, and producing charts — all in reproducible notebooks.

## Architecture

```
Agent Container          MCP Server (5001)        Notebook Container (8888)
  │                        │                        │
  ├─ add_cell(nb, code) ──►│                        │
  │                        ├─ update .ipynb in var/  │
  │◄── ok ─────────────────┤                        │
  │                        │                        │
  ├─ run_cell(nb, 3) ─────►│                        │
  │                        ├─ POST /execute ────────►│
  │                        │   {notebook, cell_idx}  ├─ runs cell on kernel
  │◄── status: running ────┤                        ├─ writes output to .ipynb
  │                        │                        │
  │  ... agent does other work ...                  │
  │                        │                        │
  ├─ read_cell(nb, 3) ────►│                        │
  │                        ├─ read .ipynb from var/  │
  │◄── output + status ────┤                        │
```

### Key Design Decisions

- **Fire-and-poll execution**: Agents submit cell execution requests and poll for results. No long-lived ZMQ or websocket connections between the MCP server and the notebook container. The MCP server makes HTTP calls and reads `.ipynb` files — nothing more.
- **Shared filesystem as source of truth**: The `.ipynb` file in `var/notebooks/` is the single source of truth. The notebook container writes outputs to it. The MCP server reads it. Flask reads it for the web UI. No separate database.
- **One kernel per notebook**: The notebook container manages kernel lifecycle internally. Each notebook gets its own kernel with persistent state (variables survive across cell executions). Kernels are lost on container restart, but `.ipynb` files retain all prior outputs and can be re-executed.
- **Containerized execution**: Scientific Python packages (pandas, numpy, scipy, matplotlib, yfinance, etc.) live only in the notebook container. The MCP server and Flask server do not need them.

## Components

### 1. Notebook Container (`Dockerfile.notebook`)

A container running a lightweight HTTP API wrapping `nbclient` and `jupyter_client` for kernel management.

**Image contents:**
- Python 3.13
- Scientific stack: `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `scikit-learn`, `yfinance`
- Notebook tooling: `nbformat`, `nbclient`, `jupyter_client`, `ipykernel`
- Lightweight HTTP framework: `flask` or `fastapi`
- Volume mount: `var/notebooks/` → `/notebooks`

**HTTP API:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/execute` | POST | Execute a cell: `{notebook, cell_index}`. Returns immediately with `{"status": "queued"}` |
| `/execute-all` | POST | Execute all cells in order: `{notebook}`. Returns immediately |
| `/status/{notebook}` | GET | Execution status: which cells are running/done/errored |
| `/kernels` | GET | List active kernels |
| `/kernels/{notebook}` | DELETE | Shut down a notebook's kernel |
| `/kernels/{notebook}/restart` | POST | Restart a notebook's kernel |

**Internal behavior:**
- On `/execute`, the service loads the `.ipynb` from disk, executes the specified cell against the notebook's kernel, writes the output back to the `.ipynb`, and updates execution status.
- Kernel lifecycle: kernels start on first execution and persist until explicitly shut down, container restart, or idle timeout.
- Execution is serialized per notebook (cell 4 waits for cell 3 to finish) but parallel across notebooks.
- Cell outputs include: stdout, stderr, display data (charts as base64 PNG), execution count, error tracebacks.

**Startup:**
```bash
# Build
podman build -f Dockerfile.notebook -t notebook-image:latest .

# Run
podman run -d --name notebook-kernel \
  -p 8888:8888 \
  -v ./var/notebooks:/notebooks:Z \
  notebook-image:latest
```

### 2. MCP Tools (`lib/mcp_server.py`)

New MCP tools registered alongside existing simulation tools. The MCP server handles notebook file manipulation (via `nbformat`) and proxies execution requests to the notebook container (via `httpx`).

| Tool | Parameters | Description |
|------|------------|-------------|
| `create_notebook` | `title` | Create a new `.ipynb` in `var/notebooks/`. Returns notebook name. |
| `add_cell` | `notebook, source, cell_type="code"` | Append a code or markdown cell to the notebook. Returns cell index. |
| `update_cell` | `notebook, cell_index, source` | Replace an existing cell's source code. |
| `delete_cell` | `notebook, cell_index` | Remove a cell from the notebook. |
| `run_cell` | `notebook, cell_index` | Submit a cell for execution. Returns immediately with status. |
| `run_all` | `notebook` | Submit all cells for sequential execution. Returns immediately. |
| `read_notebook` | `notebook` | Returns all cells with source, outputs, and execution status. |
| `read_cell` | `notebook, cell_index` | Returns a single cell's source, output, and status. |
| `list_notebooks` | | List all notebooks in `var/notebooks/`. |

**Implementation notes:**
- `create_notebook`, `add_cell`, `update_cell`, `delete_cell` operate directly on `.ipynb` files using `nbformat`. No call to the notebook container needed.
- `run_cell`, `run_all` make HTTP POST to the notebook container's `/execute` endpoint.
- `read_notebook`, `read_cell` read the `.ipynb` file from disk (which the notebook container has already updated with outputs).
- Cell outputs returned to agents should be formatted for readability: plain text and markdown rendered directly, images described as `[Chart: matplotlib figure]` with a reference, tables formatted as text.

### 3. Notebook Module (`lib/notebooks.py`)

New subsystem module following the same pattern as `lib/docs.py`, `lib/blog.py`, etc.

**Responsibilities:**
- Notebook CRUD using `nbformat`
- File I/O to `var/notebooks/`
- Notebook listing and metadata
- Session save/load (snapshot and restore notebook directory)
- Thread-safe access via `threading.Lock`

**Functions:**
```python
create_notebook(title: str, author: str) -> dict
add_cell(notebook: str, source: str, cell_type: str = "code") -> dict
update_cell(notebook: str, cell_index: int, source: str) -> dict
delete_cell(notebook: str, cell_index: int) -> dict
read_notebook(notebook: str) -> dict
read_cell(notebook: str, cell_index: int) -> dict
list_notebooks() -> list[dict]
get_notebooks_snapshot() -> dict    # for session save
restore_notebooks(data: dict)       # for session load
clear_notebooks()                   # for session new
```

### 4. REST API (`lib/webapp.py`)

New endpoints for the web UI to access notebook data.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/notebooks` | GET | List all notebooks with metadata |
| `/api/notebooks` | POST | Create a notebook |
| `/api/notebooks/{name}` | GET | Get full notebook content (cells + outputs) |
| `/api/notebooks/{name}` | DELETE | Delete a notebook |
| `/api/notebooks/{name}/cells` | GET | List cells with outputs |
| `/api/notebooks/{name}/cells` | POST | Add a cell |
| `/api/notebooks/{name}/cells/{index}` | GET | Get a single cell |
| `/api/notebooks/{name}/cells/{index}` | PUT | Update a cell |
| `/api/notebooks/{name}/cells/{index}` | DELETE | Delete a cell |
| `/api/notebooks/{name}/execute` | POST | Execute a cell or all cells (proxied to notebook container) |
| `/api/notebooks/{name}/status` | GET | Execution status |

### 5. Web UI — Notebook Viewer

A new tab in the web UI alongside Chat, Docs, GitLab, Tickets, etc.

**Features:**
- Notebook list sidebar (like the docs list)
- Selected notebook renders as a vertical cell list:
  - Markdown cells rendered as HTML
  - Code cells with syntax highlighting (use existing Prism.js or similar)
  - Cell outputs rendered inline:
    - Text/stdout as monospace
    - Tables as HTML tables
    - Charts/images as inline `<img>` from base64 data
    - Errors as red-highlighted tracebacks
  - Cell execution status indicator (pending, running, done, error)
- Auto-refresh via SSE when notebook content changes
- Read-only for the observer (agents are the editors)

**Rendering approach:** Server-side conversion using `nbconvert` to produce HTML fragments, or client-side rendering using a lightweight JS library. Server-side is simpler — Flask endpoint returns pre-rendered HTML for each notebook.

### 6. Session Management (`lib/session.py`)

Integrate notebook persistence into the existing save/load/new lifecycle.

**Save:** Copy `var/notebooks/` to `var/instances/{name}/notebooks/`

**Load:** Restore `var/notebooks/` from instance. Kernels will not be restored (they're ephemeral). If agents need prior kernel state, they re-execute the notebook.

**New:** Clear `var/notebooks/`.

### 7. Build Script (`scripts/build-notebook-image.sh`)

Similar to `build-agent-image.sh`. Builds the notebook container image.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-notebook-image:latest}"
echo "Building notebook container image: ${IMAGE}"

if ! command -v podman &>/dev/null; then
    echo "Error: podman not found." >&2
    exit 1
fi

podman build -f Dockerfile.notebook -t "$IMAGE" .
echo "Done: $IMAGE"
```

## Agent Workflow Example — Day Trading Research

1. Human posts to `#briefing`: "Research the feasibility of algorithmic day trading for retail investors"

2. Dr. Chen decomposes the topic, assigns Raj to analyze trading strategies quantitatively.

3. Raj creates a notebook and builds up an analysis:
```
create_notebook("Day Trading Strategy Backtest")

add_cell(nb, "import pandas as pd\nimport yfinance as yf\nimport numpy as np")
add_cell(nb, "# Pull 2 years of SPY data\nspy = yf.download('SPY', period='2y')\nspy.head()")
add_cell(nb, "# Simple moving average crossover\nspy['SMA_20'] = spy['Close'].rolling(20).mean()\nspy['SMA_50'] = spy['Close'].rolling(50).mean()\nspy['Signal'] = (spy['SMA_20'] > spy['SMA_50']).astype(int)\nspy['Signal'].value_counts()")
add_cell(nb, "# Backtest returns\nspy['Returns'] = spy['Close'].pct_change()\nspy['Strategy'] = spy['Signal'].shift(1) * spy['Returns']\nsharpe = np.sqrt(252) * spy['Strategy'].mean() / spy['Strategy'].std()\nprint(f'Sharpe Ratio: {sharpe:.3f}')")
add_cell(nb, "# Plot equity curve\nimport matplotlib.pyplot as plt\n(1 + spy['Strategy']).cumprod().plot(title='SMA Crossover Equity Curve')\nplt.savefig('/notebooks/equity_curve.png')\nplt.show()")

run_all(nb)
```

4. Raj polls for results:
```
read_notebook(nb)  # see all outputs, including Sharpe ratio and chart
```

5. Raj posts findings to `#technical` and creates a doc summarizing the quantitative results.

6. Elena creates her own notebook analyzing broker fee structures and their impact on the strategy's net returns.

7. Prof. Hayes reviews both notebooks via `read_notebook()`, references the computed Sharpe ratios and equity curves in his synthesis dossier.

8. Human observer watches notebooks build up in real-time via the web UI — sees code, outputs, and charts as agents execute cells.

## Process Startup

With notebooks enabled, the full startup becomes:

```bash
# Terminal 1 — Flask server
python main.py server --port 5000 --scenario research-lab

# Terminal 2 — MCP server
python main.py mcp-server --port 5001 --scenario research-lab

# Terminal 3 — Notebook kernel container
podman run -d --name notebook-kernel \
  -p 8888:8888 \
  -v ./var/notebooks:/notebooks:Z \
  notebook-image:latest

# Terminal 4 — Container orchestrator
python main.py chat --model sonnet --scenario research-lab \
  --notebook-url http://localhost:8888
```

Alternatively, the orchestrator could manage the notebook container lifecycle automatically (start on session init, stop on shutdown) similar to how it manages agent containers.

## Scenario Configuration

Add notebook settings to `scenario.yaml`:

```yaml
settings:
  enable_notebooks: true
  notebook_container_image: notebook-image:latest
  notebook_port: 8888
  notebook_packages:    # additional pip packages for the kernel
    - yfinance
    - ta-lib
```

The research-lab scenario would enable notebooks. Other scenarios can opt out by omitting the setting (defaults to false).

## Dependencies

**MCP server / host Python environment:**
- `nbformat` — notebook file manipulation
- `nbconvert` (optional) — server-side HTML rendering for web UI

**Notebook container:**
- `ipykernel` — Jupyter kernel
- `jupyter_client` — kernel management
- `nbformat` — notebook file handling
- `nbclient` — cell execution
- `flask` or `fastapi` — HTTP API
- `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `scikit-learn` — scientific stack
- `yfinance` — market data (scenario-specific, could be configurable)

## Open Questions

1. **Notebook access control** — Should notebooks have per-agent access like document folders, or should all agents in a scenario share full read/write access? For the research-lab use case, shared access makes sense.

2. **Large outputs** — Cell outputs can be large (big DataFrames, multiple charts). Should `read_notebook()` truncate outputs returned to agents? Probably yes — return first N rows of tables, indicate when output is truncated.

3. **Execution timeout** — Cells that run forever (infinite loops, massive data pulls). The notebook container should enforce a per-cell timeout (configurable, default 120s).

4. **Concurrent cell execution** — Execution is serialized per notebook (cells share kernel state). Multiple notebooks can execute in parallel. Is this sufficient?

5. **Kernel restart semantics** — When a kernel is restarted (crash, container restart, session load), all variables are lost. Agents can re-execute the notebook to rebuild state. Should there be an MCP tool for `restart_kernel(notebook)` + `run_all(notebook)` as a single operation?

6. **Orchestrator management** — Should the orchestrator start/stop the notebook container, or is it a manually managed process like the MCP server?

7. **Image rendering in agent responses** — When an agent reads a cell output containing a chart, what should the MCP tool return? Options: base64 data (too large), text description ("Chart: line plot showing equity curve"), or a URL the agent can reference ("see chart at notebook X, cell 3").

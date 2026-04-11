"""CLI argument parser for the multi-agent organization system."""

import argparse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Multi-agent organization chat system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── server subcommand ────────────────────────────────────────────
    server_parser = subparsers.add_parser(
        "server",
        help="Start the Flask chat server",
    )
    server_parser.add_argument(
        "--port", type=int, default=5000,
        help="Port to listen on (default: 5000)",
    )
    server_parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    server_parser.add_argument(
        "--scenario", type=str, default=None,
        help="Scenario to auto-start (omit to wait for New/Load in UI)",
    )

    # ── chat subcommand (container orchestrator) ──────────────────────
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start the container orchestrator (agents run as podman containers with MCP tools)",
    )
    chat_parser.add_argument(
        "--personas", type=str, default=None,
        help="Comma-separated list of personas to activate (default: all)",
    )
    chat_parser.add_argument(
        "--server-url", type=str, default="http://127.0.0.1:5000",
        help="Chat server URL (default: http://127.0.0.1:5000)",
    )
    chat_parser.add_argument(
        "--model", type=str, default="sonnet",
        choices=["sonnet", "opus", "haiku"],
        help="Claude model to use (default: sonnet)",
    )
    chat_parser.add_argument(
        "--max-rounds", type=int, default=5,
        help="Maximum discussion waves per trigger (default: 5)",
    )
    chat_parser.add_argument(
        "--max-auto-rounds", type=int, default=0,
        help="Maximum autonomous continuation rounds (0 = unlimited, default: 0)",
    )
    chat_parser.add_argument(
        "--poll-interval", type=float, default=5.0,
        help="Seconds between polling for new messages (default: 5.0)",
    )
    chat_parser.add_argument(
        "--scenario", type=str, default=None,
        help="Scenario to auto-start (omit to wait for New/Load in UI)",
    )
    chat_parser.add_argument(
        "--mcp-port", type=int, default=5001,
        help="MCP server port (default: 5001)",
    )
    chat_parser.add_argument(
        "--container-image", type=str, default="agent-image:latest",
        help="Container image for agents (default: agent-image:latest)",
    )
    chat_parser.add_argument(
        "--container-timeout", type=int, default=300,
        help="Maximum seconds per container run (default: 300)",
    )
    chat_parser.add_argument(
        "--max-turns", type=int, default=50,
        help="Maximum Claude turns per container (default: 50)",
    )
    chat_parser.add_argument(
        "--max-concurrent", type=int, default=4,
        help="Maximum concurrent agent containers per tier (default: 4)",
    )
    chat_parser.add_argument(
        "--done-timeout", type=int, default=120,
        help="Seconds to wait for agents to signal done before advancing tier (default: 120)",
    )
    chat_parser.add_argument(
        "--mcp-host", type=str, default=None,
        help="Hostname containers use to reach MCP server on host "
             "(default: auto-detect based on platform — "
             "host.containers.internal on macOS, host gateway IP on Linux)",
    )

    # ── mcp-server subcommand ─────────────────────────────────────────
    mcp_parser = subparsers.add_parser(
        "mcp-server",
        help="Start the MCP tool server for v3 agent architecture",
    )
    mcp_parser.add_argument(
        "--port", type=int, default=5001,
        help="Port to listen on (default: 5001)",
    )
    mcp_parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
    )
    mcp_parser.add_argument(
        "--flask-url", type=str, default="http://127.0.0.1:5000",
        help="Flask server URL to proxy to (default: http://127.0.0.1:5000)",
    )
    mcp_parser.add_argument(
        "--scenario", type=str, default=None,
        help="Scenario to load at startup (omit to configure later via orchestrator)",
    )
    return parser.parse_args(argv)

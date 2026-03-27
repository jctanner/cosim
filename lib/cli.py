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

    # ── chat subcommand ──────────────────────────────────────────────
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start the agent orchestrator",
    )
    chat_parser.add_argument(
        "--personas", type=str, default=None,
        help="Comma-separated list of personas to activate (default: all). "
             "Options: pm, engmgr, architect, senior, support, sales",
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
        help="Maximum discussion rounds per customer message (default: 5)",
    )
    chat_parser.add_argument(
        "--poll-interval", type=float, default=5.0,
        help="Seconds between polling for new messages (default: 5.0)",
    )
    return parser.parse_args(argv)

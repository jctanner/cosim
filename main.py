#!/usr/bin/env python3
"""Multi-agent organization chat system — AI personas collaborating via shared chat."""

import sys
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from lib.cli import parse_args

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    print(
        "Error: .env file not found\n"
        "\n"
        f"Expected location: {env_path}\n"
        "\n"
        "Create a .env file with at minimum:\n"
        "\n"
        "  CLAUDE_CODE_USE_VERTEX=1\n"
        "  CLOUD_ML_REGION=us-east5\n"
        "  ANTHROPIC_VERTEX_PROJECT_ID=<your-project>\n"
        "\n"
        "The Claude Agent SDK requires valid Vertex AI credentials.",
        file=sys.stderr,
    )
    sys.exit(1)
load_dotenv(dotenv_path=env_path)


if __name__ == "__main__":
    args = parse_args()

    try:
        if args.command == "server":
            from lib.webapp import create_app
            if args.scenario:
                # Auto-start a session with the specified scenario
                from lib.scenario_loader import load_scenario
                from lib.session import set_scenario, new_session
                load_scenario(args.scenario)
                set_scenario(args.scenario)
            app = create_app()
            if args.scenario:
                # Queue a restart command so the orchestrator auto-starts agents
                from lib.webapp import _command_lock, _orchestrator_commands
                with _command_lock:
                    _orchestrator_commands.append({"action": "restart", "scenario": args.scenario})
            app.run(host=args.host, port=args.port, debug=False)
        elif args.command == "chat":
            from lib.container_orchestrator import run_container_orchestrator
            while True:
                try:
                    asyncio.run(run_container_orchestrator(args))
                    break
                except (asyncio.CancelledError, BaseException) as e:
                    if isinstance(e, KeyboardInterrupt):
                        raise
                    import traceback
                    print(f"\nOrchestrator crashed ({type(e).__name__}): {e}")
                    traceback.print_exc()
                    print("Restarting in 3 seconds...")
                    import time
                    time.sleep(3)
        elif args.command == "mcp-server":
            import uvicorn
            from lib.mcp_server import build_app
            app = build_app(scenario_name=args.scenario, flask_url=args.flask_url)
            uvicorn.run(app, host=args.host, port=args.port)
        else:
            print("Use 'server', 'chat', or 'mcp-server' subcommand. See --help.", file=sys.stderr)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

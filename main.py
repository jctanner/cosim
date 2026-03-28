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

    # Load scenario config before any consumer imports
    from lib.scenario_loader import load_scenario
    from lib.session import set_scenario
    load_scenario(args.scenario)
    set_scenario(args.scenario)

    try:
        if args.command == "server":
            from lib.webapp import create_app
            app = create_app()
            app.run(host=args.host, port=args.port, debug=False)
        elif args.command == "chat":
            from lib.orchestrator import run_orchestrator
            asyncio.run(run_orchestrator(args))
        else:
            print("Use 'server' or 'chat' subcommand. See --help.", file=sys.stderr)
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

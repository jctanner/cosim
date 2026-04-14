"""Flask chat server with SSE broadcast and web UI.

This package replaces the former monolithic lib/webapp.py.
All backward-compatible imports are re-exported here.
"""

from flask import Flask

from lib.gitlab import GITLAB_DIR
from lib.tickets import TICKETS_DIR
from lib.webapp.state import (
    # Path constants
    CHAT_LOG, DOCS_DIR, LOGS_DIR,
    # Messages
    _messages, _lock, _subscribers, _sub_lock,
    # Channels
    _channels, _channel_members, _channel_lock,
    # Documents
    _docs_index, _docs_lock,
    # Folders
    _folders, _folder_access, _folder_lock,
    # GitLab
    _gitlab_repos, _gitlab_commits, _gitlab_lock,
    # Tickets
    _tickets, _tickets_lock,
    # Recaps
    _recaps,
    # Agent state
    _agent_online, _agent_firing, _agent_verbosity,
    _agent_last_activity, _agent_online_lock,
    _agent_thoughts, _agent_thoughts_lock,
    # Orchestrator
    _orchestrator_status, _orchestrator_lock,
    _orchestrator_commands, _command_lock,
)
from lib.webapp.helpers import (
    _init_channels, _init_folders, _init_docs, _init_gitlab,
    _init_tickets, _init_agent_online, _load_chat_log,
)

# Route blueprints
from lib.webapp.routes.index import bp as index_bp
from lib.webapp.routes.channels import bp as channels_bp
from lib.webapp.routes.messages import bp as messages_bp
from lib.webapp.routes.documents import bp as documents_bp
from lib.webapp.routes.gitlab import bp as gitlab_bp
from lib.webapp.routes.tickets import bp as tickets_bp
from lib.webapp.routes.orchestrator import bp as orchestrator_bp
from lib.webapp.routes.npcs import bp as npcs_bp
from lib.webapp.routes.events import bp as events_bp
from lib.webapp.routes.recaps import bp as recaps_bp
from lib.webapp.routes.emails import bp as emails_bp
from lib.webapp.routes.memos import bp as memos_bp
from lib.webapp.routes.blog import bp as blog_bp
from lib.webapp.routes.sessions import bp as sessions_bp
from lib.webapp.routes.misc import bp as misc_bp


def create_app() -> Flask:
    """Create and configure the Flask chat application."""
    app = Flask(__name__)

    # Initialize channels, folders, and docs
    _init_channels()
    print(f"Channels initialized: {sorted(_channels.keys())}")

    _init_folders()
    print(f"Folders initialized: {sorted(_folders.keys())}")

    _init_docs()
    print(f"Docs directory ready: {DOCS_DIR}  ({len(_docs_index)} existing docs)")

    _init_gitlab()
    print(f"GitLab storage ready: {GITLAB_DIR}  ({len(_gitlab_repos)} existing repos)")

    _init_tickets()
    print(f"Tickets storage ready: {TICKETS_DIR}  ({len(_tickets)} existing tickets)")

    _init_agent_online()

    _load_chat_log()
    print(f"Chat log: {len(_messages)} messages loaded")

    # Register all blueprints
    app.register_blueprint(index_bp)
    app.register_blueprint(channels_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(gitlab_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(orchestrator_bp)
    app.register_blueprint(npcs_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(recaps_bp)
    app.register_blueprint(emails_bp)
    app.register_blueprint(memos_bp)
    app.register_blueprint(blog_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(misc_bp)

    return app

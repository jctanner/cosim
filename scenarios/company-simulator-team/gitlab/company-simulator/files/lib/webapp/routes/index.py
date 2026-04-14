"""Root route — serves the single-page web UI."""

from flask import Blueprint

from lib.webapp.template import WEB_UI

bp = Blueprint("index", __name__)


@bp.route("/")
def index():
    return WEB_UI

"""The phone surface.

A separate set of screens, not the desktop pages reshaped. Two attempts at reshaping them
failed the same way: the admin pages carry roughly 750 lines of their own layout CSS built for a
desk, and overriding that at runtime produces a desktop page in a costume, which is what it
looked like on a real phone.

So these screens render their own markup, load their own stylesheet, and never touch
templates/base.html or Bootstrap. What they share with the desktop is the data and the write
paths, which are already good: overview.gather() for the figures, campaign_progress for
candidate state, /progress/update to save an edit. Nothing here duplicates that logic.

One rule shapes every screen: never ship a long list to a phone. /progress renders 504 rows and
the spend plan rail 203 districts, and no stylesheet makes either of those usable held in one
hand. Lists here are searched and paged on the server.
"""
import logging

from flask import Blueprint, render_template, request, jsonify, url_for

logger = logging.getLogger(__name__)

mobile_bp = Blueprint('m', __name__, url_prefix='/m')

get_db_connection = None
release_db_connection = None
require_feature_access = None


def init_mobile(db_conn_func, db_release_func, feature_gate):
    """Wire in the app's database pool and the same access gate the private pages use, so a
    phone shows exactly what that person could see at a desk and nothing more."""
    global get_db_connection, release_db_connection, require_feature_access
    get_db_connection = db_conn_func
    release_db_connection = db_release_func
    require_feature_access = feature_gate
    _register_routes()


def _register_routes():
    """Routes are declared here because the access decorator arrives at init time."""

    @mobile_bp.route('/')
    @require_feature_access('campaign_plan')
    def home():
        """Where things stand. Same figures as /private/overview, laid out for one hand."""
        import overview as OV
        return render_template('m/home.html', d=OV.gather(), screen='home')

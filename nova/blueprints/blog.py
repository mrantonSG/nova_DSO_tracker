"""
Blog Blueprint — Community astrophotography sharing.

Routes are implemented in nova/__init__.py (monolith pattern).
This file only defines the blueprint object.
"""

from flask import Blueprint

blog_bp = Blueprint("blog", __name__)

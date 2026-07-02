import os
from config.settings.base import *  # noqa: F401,F403 — re-exports BASE_DIR, DATABASES, etc.

DEBUG = False
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")

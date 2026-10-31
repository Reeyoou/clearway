"""Vercel serverless function: reuses the app's request handler unchanged."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from adtrace.app import H  # noqa: E402


class handler(H):
    pass

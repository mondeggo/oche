"""Errors that are safe to show: actionable messages without upstream bodies or credentials."""

import json

import httpx


class ConnectionProblem(Exception):
    """Safe, actionable message. Never include upstream bodies or credentials."""


def safe_error(exc: BaseException) -> str:
    if isinstance(exc, ConnectionProblem):
        return str(exc)
    if isinstance(exc, OSError):
        return "Network connection failed. Check the network and configured address."
    if isinstance(exc, httpx.HTTPError):
        return "HTTP connection failed. Check the network and configured address."
    if isinstance(exc, json.JSONDecodeError):
        return "Invalid JSON response."
    return "Connection interrupted. Retrying automatically."

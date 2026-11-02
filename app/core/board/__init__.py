"""Client for the board API that vision engines serve.

Autodarts defines it (``GET /api/state``, ``WS /api/events?type=state``), and any
engine that scores games speaks it, so Play reads every engine through this client.
"""

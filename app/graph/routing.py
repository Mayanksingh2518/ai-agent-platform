"""Conditional routing out of the supervisor."""

from __future__ import annotations

from app.graph.state import AgentState


def route_from_supervisor(state: AgentState) -> str:
    return state["task"]

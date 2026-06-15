"""Security reviewer agent."""

from __future__ import annotations

from app.agents.reviewers.base import ReviewerAgent
from app.llm.prompts import SECURITY_SYSTEM


class SecurityReviewer(ReviewerAgent):
    name = "security"
    component = "security"
    system = SECURITY_SYSTEM
    focus_query_template = (
        "authentication, authorization, input validation, secrets handling, "
        "subprocess/shell usage, and trust boundaries"
    )

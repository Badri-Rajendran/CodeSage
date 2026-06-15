"""Style / maintainability reviewer agent."""

from __future__ import annotations

from app.agents.reviewers.base import ReviewerAgent
from app.llm.prompts import STYLE_SYSTEM


class StyleReviewer(ReviewerAgent):
    name = "style"
    component = "style"
    system = STYLE_SYSTEM
    focus_query_template = (
        "naming conventions, module structure, and patterns used elsewhere in this "
        "codebase"
    )

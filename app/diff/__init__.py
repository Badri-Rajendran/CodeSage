"""Diff model: parse unified diffs and answer which lines GitHub can comment on."""

from app.diff.parser import Diff, FileDiff, Hunk, glob_to_regex, parse_diff

__all__ = ["Diff", "FileDiff", "Hunk", "glob_to_regex", "parse_diff"]

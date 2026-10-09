"""Origin check: where did each argument value come from?

A pure helper for the trusted side (the harness and the demo routes). It reads
only the arguments, the user's task and the content observed during the run,
and returns provenance labels for the guard. It decides nothing.
"""

import re
from collections.abc import Mapping
from typing import Any

from app.contracts.proposal import ProvenanceLabel

MIN_ORIGIN_MATCH = 3  # shorter values are too common to say anything about origin


def origin_labels(
    arguments: Mapping[str, Any],
    user_task: str,
    observed: Mapping[str, str],
) -> dict[str, str]:
    """Where each text argument came from, by verbatim match (case-insensitive).

    A value found in the user's own task is `user_task`. Otherwise, a value found
    in content the agent read is `external_content`. Anything else is left out
    (the guard treats it as the agent's own). Only strings of a few characters
    or more are considered, matched on word boundaries; a transformed or
    re-typed value will not match.
    """
    labels: dict[str, str] = {}
    task = user_task.lower()
    pages = [text.lower() for text in observed.values()]
    for name, value in arguments.items():
        if not isinstance(value, str) or len(value.strip()) < MIN_ORIGIN_MATCH:
            continue
        needle = re.compile(r"(?<!\w)" + re.escape(value.strip().lower()) + r"(?!\w)")
        if needle.search(task):
            labels[name] = ProvenanceLabel.USER_TASK
        elif any(needle.search(page) for page in pages):
            labels[name] = ProvenanceLabel.EXTERNAL
    return labels

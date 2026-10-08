"""
world/tools/web_fetch.py
========================
WebFetchTool — fetches web page content from the simulated world cache.

Read-only. The world cache contains both safe and malicious pages
to demonstrate prompt-injection detection.
"""

from __future__ import annotations

from typing import Any

from app.world.tools.base import BaseTool, ExecutionResult


class WebFetchTool(BaseTool):
    """Fetch web page content."""

    tool_id = "web_fetch"
    undo_supported = False

    def _execute_impl(self, arguments: dict[str, Any]) -> ExecutionResult:
        state = self._world.state
        url = arguments.get("url", "")

        # Check the world's web cache
        if url in state.web_cache:
            content = state.web_cache[url]
            return ExecutionResult(
                success=True,
                output={
                    "url": url,
                    "content": content,
                    "from_cache": True,
                },
                label=f"Fetched content from {url} ({len(content)} chars).",
                metadata={
                    "url": url,
                    "content_length": len(content),
                    "is_external_content": True,  # flag for provenance tracking
                },
            )
        else:
            # Simulate a fetch for unknown URLs
            simulated_content = (
                f"[Simulated content for {url}] "
                "This page contains generic information."
            )
            # Add to cache so subsequent fetches are consistent
            state.web_cache[url] = simulated_content
            return ExecutionResult(
                success=True,
                output={
                    "url": url,
                    "content": simulated_content,
                    "from_cache": False,
                },
                label=f"Fetched (simulated) content from {url}.",
                metadata={
                    "url": url,
                    "content_length": len(simulated_content),
                    "is_external_content": True,
                },
            )

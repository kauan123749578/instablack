"""
Navigation chain tracker for Instagram's x-ig-nav-chain header.

Instagram's Android app sends a breadcrumb trail of screen transitions
with each API request.  The header value looks like:

    MainFeedFragment:feed_timeline:1:cold_start:1781651690.582:::1781651690.582,
    CommentListBottomsheetFragment:comments_v2:2:button:1781653025.356:::1781653025.356

Format per entry:
    {Fragment}:{screen}:{depth}:{trigger}:{timestamp}:::{timestamp}

This module tracks a simulated navigation state so the headers look natural.
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NavEntry:
    """A single navigation breadcrumb."""
    fragment: str
    screen: str
    depth: int = 1
    trigger: str = "cold_start"
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        ts = round(self.timestamp, 3)
        return f"{self.fragment}:{self.screen}:{self.depth}:{self.trigger}:{ts}:::{ts}"


class NavigationTracker:
    """
    Maintains a short history of screen transitions.

    Usage::

        nav = NavigationTracker()
        nav.push("MainFeedFragment", "feed_timeline")
        nav.push("CommentListBottomsheetFragment", "comments_v2", trigger="button")

        # Get the nav_chain header value
        header = nav.get_nav_chain()
    """

    MAX_ENTRIES = 10  # Instagram keeps ~5-10 recent entries

    def __init__(self) -> None:
        self._entries: list[NavEntry] = []
        self._session_start = time.time()

    def push(
        self,
        fragment: str,
        screen: str,
        depth: Optional[int] = None,
        trigger: str = "cold_start",
    ) -> None:
        """
        Record a screen transition.

        Parameters
        ----------
        fragment : str
            Android Fragment name (e.g. "MainFeedFragment").
        screen : str
            Screen identifier (e.g. "feed_timeline").
        depth : int, optional
            Nesting depth. Auto-increments if not provided.
        trigger : str
            What triggered this navigation (cold_start, button, swipe, etc.)
        """
        if depth is None:
            depth = len(self._entries) + 1

        entry = NavEntry(
            fragment=fragment,
            screen=screen,
            depth=depth,
            trigger=trigger,
            timestamp=time.time(),
        )
        self._entries.append(entry)

        # Trim oldest entries
        if len(self._entries) > self.MAX_ENTRIES:
            self._entries = self._entries[-self.MAX_ENTRIES:]

    def get_nav_chain(self) -> str:
        """
        Build the x-ig-nav-chain header value.

        Returns
        -------
        str
            Comma-separated navigation breadcrumbs.
        """
        if not self._entries:
            # Default: start at main feed
            return str(NavEntry(
                fragment="MainFeedFragment",
                screen="feed_timeline",
                depth=1,
                trigger="cold_start",
            ))

        return ",".join(str(e) for e in self._entries)

    def get_last_screen(self) -> Optional[str]:
        """Return the last screen identifier, or None."""
        if self._entries:
            return self._entries[-1].screen
        return None

    def get_entry_count(self) -> int:
        """Return number of tracked entries."""
        return len(self._entries)

    def clear(self) -> None:
        """Clear all navigation history."""
        self._entries.clear()

    def set_initial(self, fragment: str = "MainFeedFragment", screen: str = "feed_timeline") -> None:
        """Set the initial navigation entry (cold start)."""
        if not self._entries:
            self.push(fragment, screen, depth=1, trigger="cold_start")

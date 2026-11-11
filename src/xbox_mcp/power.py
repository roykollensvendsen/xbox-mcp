# SPDX-License-Identifier: GPL-3.0-or-later
"""The two steps of waking or sleeping the console, and when it is night.

A power question hands out a token; only the owner's yes, passed back with
that token within 30 seconds, sends the command. One question is open at a
time, and a token works once.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

#: Seconds a power question stays open for the owner's yes.
CONFIRM_WITHIN = 30.0

Action = Literal["wake", "sleep"]
#: What a question asks about: power, sending the console to Home, or a start over a running game.
Kind = Literal["wake", "sleep", "home", "launch"]


@dataclass(frozen=True)
class Question:
    """A question waiting for the owner's yes."""

    token: str
    action: Kind
    console_id: str
    asked_at: float
    #: What ran in front when asked (home, launch), by its AUMID, checked again at the yes.
    over: str | None = None
    #: The game it is about: the one in front (home), or the one to start (launch).
    name: str | None = None
    #: The game to start (launch), by its store id and AUMID.
    product_id: str | None = None
    aumid: str | None = None


class Questions:
    """The one open power question, if there is one."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Questions timed by `clock`, in seconds."""
        self._clock = clock
        self._open: Question | None = None

    def ask(self, action: Kind, console_id: str, **about: str | None) -> Question:
        """Open a question, closing any that was open."""
        self._open = Question(secrets.token_urlsafe(12), action, console_id, self._clock(), **about)
        return self._open

    def take(self, token: str, kinds: tuple[Kind, ...] = ("wake", "sleep")) -> Question | None:
        """The open question of one of `kinds` this token answers, while it is fresh; None otherwise."""
        question = self._open
        if question is None or not secrets.compare_digest(question.token, token):
            return None
        # RULE: a token answers only the question it was asked for
        if question.action not in kinds:
            return None
        # RULE: a token older than thirty seconds is refused
        if self._clock() - question.asked_at > CONFIRM_WITHIN:
            self._open = None
            return None
        return question

    def spend(self, question: Question) -> None:
        """Close `question`, so its token never works again."""
        # RULE: a token is single-use
        if self._open == question:
            self._open = None


def is_night(hour: int, night: tuple[int, int]) -> bool:
    """Whether `hour` falls in the night that runs from ``night[0]`` to ``night[1]``."""
    starts, ends = night
    if starts > ends:  # over midnight, as 22 to 07
        return hour >= starts or hour < ends
    return starts <= hour < ends


def local_hour() -> int:
    """The hour of the day now, by the computer's own clock."""
    return time.localtime().tm_hour

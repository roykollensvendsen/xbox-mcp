# SPDX-License-Identifier: GPL-3.0-or-later
"""Each tool answers within the time the voice gives it, and finishes soon after.

The Claude app gives up on a tool after about 15 seconds, and claude-voice cuts
a plugin off after 30. So each tool's longest path, added up from the waits it
makes, has to fit: the answer within the server's deadline, or else the work
that carries on past it within half a minute, its result said on the next
answer. A new wait, or a longer one, goes into the sum here.
"""

from xbox_mcp import server, service, tv
from xbox_mcp.drivers import cloud

#: How long the Claude app waits for a tool, in seconds (the plugin standard's rule 3).
VOICE_LIMIT = 15.0
#: How long claude-voice lets a plugin's call run.
BRIDGE_LIMIT = 30.0
CALL = cloud.CALL_WITHIN

#: Each tool's longest path that the voice hears within the deadline, once the console is known.
#: The server looks the console up at start (cli._serve), and only once (XboxService.console).
ANSWERED = {
    "status: the status, then the installed apps": 2 * CALL,
    "power question: the status, then the TV at night": CALL + tv.TV_WITHIN,
}
#: Paths that may run past the deadline. The voice hears "it carries on", and what they end with
#: is said at the start of the next answer (test_server's test_what_a_slow_tool_ends_with_...).
FINISHED = {
    "status asked before the start-up lookup is done: the consoles, the status, the apps": 3 * CALL,
    "power confirm: the TV at night, the command, the watch, then the TV": (
        tv.TV_WITHIN + CALL + service.READ_BACK_WITHIN + tv.TV_WITHIN
    ),
    "launch, console unknown: the consoles, the status, the apps, the start, the watch": (
        4 * CALL + service.READ_BACK_WITHIN
    ),
}


def test_the_server_answers_before_the_voice_gives_up():
    assert server.ANSWER_WITHIN + 2.0 <= VOICE_LIMIT


def test_a_tools_longest_path_is_answered_within_the_servers_deadline():
    for path, seconds in ANSWERED.items():
        assert seconds <= server.ANSWER_WITHIN, (path, seconds)


def test_a_tools_longest_path_finishes_within_half_a_minute():
    for path, seconds in FINISHED.items():
        assert seconds <= BRIDGE_LIMIT, (path, seconds)


def test_waiting_for_hdmi_cec_fits_inside_the_tv_step():
    assert tv.CEC_SETTLE + 2.0 <= tv.TV_WITHIN

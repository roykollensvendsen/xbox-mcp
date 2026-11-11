# SPDX-License-Identifier: GPL-3.0-or-later
"""Each tool answers within the time the voice gives it, and finishes soon after.

The Claude app gives up on a tool after about 15 seconds, and claude-voice cuts
a plugin off after 30. So each tool's longest path, added up from the waits it
makes, has to fit: the answer within the server's deadline, and the work that
carries on past it within half a minute. A new wait, or a longer one, goes
into the sum here.
"""

from xbox_mcp import server, service, tv
from xbox_mcp.drivers import cloud

#: How long the Claude app waits for a tool, in seconds (the plugin standard's rule 3).
VOICE_LIMIT = 15.0
#: How long claude-voice lets a plugin's call run.
BRIDGE_LIMIT = 30.0
CALL = cloud.CALL_WITHIN

#: Each tool's longest path, once the console is known (it is looked up once and kept).
ANSWERED = {
    "status: the status, then the installed apps": 2 * CALL,
    "power question: the status, then the TV at night": CALL + tv.TV_WITHIN,
}
FINISHED = {
    "power confirm: the TV at night, the command, the watch, then the TV": (
        tv.TV_WITHIN + CALL + service.READ_BACK_WITHIN + tv.TV_WITHIN
    ),
    "launch: the status, the apps, the start, then the watch": 3 * CALL + service.READ_BACK_WITHIN,
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

# SPDX-License-Identifier: GPL-3.0-or-later
"""Each rule of the voice plugins' shared standard has a test here named after it.

The standard is claude-voice's docs/plugin-standard.md. Rules 1 to 11 each map
to the tests that guard them in xbox-mcp; renaming or deleting one of those
tests turns this red, so a rule cannot lose its guard quietly. Rule 12 is this
file, and its second half, asking the voice what it sees after a change to a
name or a first line, is a step for whoever changes one.
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).parent

#: Each rule of the standard, and the tests in xbox-mcp that guard it.
STANDARD = {
    1: [
        "test_tool_names.py::test_no_two_tool_names_can_be_confused",
        "test_tool_names.py::test_the_names_that_sound_alike_open_with_different_words",
    ],
    2: ["test_server.py::test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full"],
    3: [
        "test_server.py::test_a_tools_longest_wait_fits_in_the_voices_time",
        "test_voice_time_limit.py::test_a_tools_longest_path_is_answered_within_the_servers_deadline",
        "test_voice_time_limit.py::test_a_tools_longest_path_finishes_within_half_a_minute",
        "test_server.py::test_a_tool_still_working_at_the_deadline_says_it_carries_on",
        "test_cold_start.py::test_a_cold_start_never_waits_on_the_xbox",
    ],
    4: [
        "test_server.py::test_every_tool_answers_with_a_sentence_even_when_it_fails",
        "test_next_step.py::test_every_failure_says_what_to_do_next",
    ],
    5: [
        "test_power.py::test_a_command_is_said_as_done_only_once_the_status_reads_it_back",
        "test_launch.py::test_a_start_is_said_as_done_only_once_the_xbox_shows_it",
    ],
    6: [
        "test_status.py::test_a_cloud_that_does_not_answer_is_not_called_on_or_off",
        "test_night.py::test_at_night_a_tv_whose_state_is_unknown_counts_as_off",
    ],
    7: [
        "test_power.py::test_nothing_is_sent_on_the_question_alone",
        "test_power.py::test_a_token_is_single_use",
        "test_power.py::test_a_token_older_than_thirty_seconds_is_refused",
        "test_power.py::test_a_token_is_spent_only_once_the_command_was_sent",
        "test_home.py::test_a_stop_asks_first_naming_the_game_and_sends_nothing",
        "test_home.py::test_a_stop_is_checked_again_at_the_yes",
        "test_home.py::test_a_token_answers_only_the_question_it_was_asked_for",
        "test_launch.py::test_a_start_over_a_running_game_asks_first_naming_it",
        "test_launch.py::test_a_start_over_a_game_is_checked_again_at_the_yes",
    ],
    8: ["test_tv.py::test_only_the_tv_status_and_input_tools_are_ever_called"],
    9: ["test_server.py::test_the_tools_that_only_look_are_marked_read_only_and_no_others"],
    10: [
        "test_cloud.py::test_a_token_file_others_can_read_is_refused",
        "test_cloud.py::test_tokens_are_written_readable_by_the_owner_alone",
        "test_private_data_scan.py::test_a_value_from_the_settings_file_is_never_committed",
        "test_private_data_scan.py::test_an_email_address_is_never_committed",
    ],
    11: ["test_server.py::test_every_simulated_answer_says_simulated_first"],
}


def _defined(file):
    tree = ast.parse((TESTS / file).read_text())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_each_rule_has_a_test_named_after_it():
    assert set(STANDARD) == set(range(1, 12))
    missing = [
        f"rule {rule}: {test}"
        for rule, tests in STANDARD.items()
        for test in tests
        if test.split("::")[1] not in _defined(test.split("::")[0])
    ]
    assert not missing, missing

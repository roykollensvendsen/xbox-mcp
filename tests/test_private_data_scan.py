# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing committed says where the owner's Xbox is, whose it is, or how to reach it."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import private_data_scan


def test_a_home_network_address_is_never_committed():
    # Built at run time, so this file does not itself hold what it forbids.
    parts = (("10", "0", "0", "7"), ("172", "20", "1", "2"), ("192", "168", "1", "50"))
    for address in (".".join(p) for p in parts):
        assert private_data_scan.offences(f"console at http://{address}:5050", set()), address
    mac = ":".join(["F0", "12", "34", "56", "78", "9A"])  # noqa: FLY002
    assert private_data_scan.offences(f"mac {mac}", set())


def test_documentation_addresses_are_allowed():
    text = "console = 192.0.2.10, mac = 00:00:5E:00:53:01, version 1.2.3.4, 8.8.8.8"
    assert private_data_scan.offences(text, set()) == []


def test_a_value_from_the_settings_file_is_never_committed():
    assert private_data_scan.offences("token = 'abc123secret'", {"abc123secret"})
    assert private_data_scan.offences("nothing here", {"abc123secret"}) == []


def test_an_email_address_is_never_committed():
    # Built at run time, so this file does not itself hold what it forbids.
    address = "someone" + "@" + "mail.invalid-home.net"
    assert private_data_scan.offences(f"signed in as {address}", set())


def test_example_and_no_reply_email_addresses_are_allowed():
    text = "someone@example.com, 1+bot@users.noreply.github.com, noreply@anthropic.com"
    assert private_data_scan.offences(text, set()) == []


def test_the_tracked_tree_is_clean():
    assert private_data_scan.main(["--tracked"]) == 0


def test_the_room_and_console_names_count_but_not_the_device_or_titles(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[xbox]\nconsole = "Front room box"\n'
        '[tv]\nname = "upstairs den"\ndevice = "Game box"\n'
        '[launch]\ntitles = ["Forza Horizon 5"]\n'
    )
    assert private_data_scan.private_values(path) == {"Front room box", "upstairs den"}

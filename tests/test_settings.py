# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's settings come from ~/.config/xbox-mcp, never from this repository."""

import pytest

from xbox_mcp.settings import NIGHT, Settings, SettingsError, load_settings, tokens_path


def test_an_absent_file_means_nothing_is_set(tmp_path):
    settings = load_settings(tmp_path / "config.toml")
    assert settings == Settings()
    assert settings.night == NIGHT


def test_the_settings_are_read(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[xbox]\nconsole = "Front box"\n'
        '[launch]\ntitles = ["Forza Horizon 5", "Minecraft"]\n'
        '[tv]\ncommand = ["tv-mcp", "serve"]\nname = "the den"\ndevice = "Xbox"\n'
        "[night]\nstarts = 23\nends = 6\n"
    )
    settings = load_settings(path)
    assert settings.console == "Front box"
    assert settings.titles == ("Forza Horizon 5", "Minecraft")
    assert settings.tv == {"command": ["tv-mcp", "serve"], "name": "the den", "device": "Xbox"}
    assert settings.night == (23, 6)


def test_a_night_hour_outside_the_day_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[night]\nstarts = 25\nends = 7\n")
    with pytest.raises(SettingsError):
        load_settings(path)


def test_titles_must_be_a_list_of_names(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[launch]\ntitles = "Forza"\n')
    with pytest.raises(SettingsError):
        load_settings(path)


def test_the_tokens_live_beside_the_settings(tmp_path):
    assert tokens_path(tmp_path / "config.toml") == tmp_path / "tokens.json"


def test_a_night_that_starts_when_it_ends_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[night]\nstarts = 22\nends = 22\n")
    with pytest.raises(SettingsError, match="differ"):
        load_settings(path)

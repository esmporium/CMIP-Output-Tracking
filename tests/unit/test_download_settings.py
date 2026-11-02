"""
Reading the download settings out of the environment
"""

from pathlib import Path

import pytest

from local.downloads import DownloadSettings, MissingDownloadSettingError


def test_the_root_is_read_from_the_environment():
    settings = DownloadSettings.from_env({"DOWNLOAD_ROOT": "/data/azehrung/cmip-data"})

    assert settings.root == Path("/data/azehrung/cmip-data")


def test_a_missing_root_is_refused_by_name():
    # The point of this test is the *name*: the message has to say which variable to
    # go and set, because the person reading it is reading it from a cron log.
    with pytest.raises(MissingDownloadSettingError, match="DOWNLOAD_ROOT") as caught:
        DownloadSettings.from_env({})

    assert caught.value.name == "DOWNLOAD_ROOT"
    assert ".env" in str(caught.value)


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_a_blank_root_counts_as_missing(value):
    # A `.env` line left as `DOWNLOAD_ROOT=` is the likeliest way to get here, and
    # downloading into the current working directory instead would be much worse
    # than refusing.
    with pytest.raises(MissingDownloadSettingError):
        DownloadSettings.from_env({"DOWNLOAD_ROOT": value})


def test_surrounding_whitespace_is_stripped():
    # A path typed into a `.env` file picks up a trailing space invisibly, and a
    # directory whose name ends in one is not the directory anybody meant.
    settings = DownloadSettings.from_env({"DOWNLOAD_ROOT": "  /data/cmip  \n"})

    assert settings.root == Path("/data/cmip")


def test_a_home_relative_root_is_expanded():
    # `~` is what somebody writes in a `.env` file by hand, and cron does not expand
    # it, so a literal `~/cmip-data` directory is what we would otherwise create.
    settings = DownloadSettings.from_env({"DOWNLOAD_ROOT": "~/cmip-data"})

    assert "~" not in str(settings.root)
    assert settings.root.is_absolute()

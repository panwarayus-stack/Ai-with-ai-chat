"""Input validation and message formatting tests."""

from __future__ import annotations

import pytest

from src.lib.formatting import chunk_message
from src.lib.validation import (
    CommandInputError,
    clean_choice,
    clean_city,
    clean_name,
    clean_phone,
    clean_query,
    clean_two_names,
    clean_username,
    parse_command,
    require_args,
)


def test_parse_command_case_insensitive():
    assert parse_command("/Info") == ("info", "")
    assert parse_command("/INFO rest here") == ("info", "rest here")
    assert parse_command("/help?") == ("help", "")
    assert parse_command("hello") == ("", "")
    assert parse_command("/") == ("", "")


def test_require_args_usage_message():
    with pytest.raises(CommandInputError) as exc:
        require_args("", "/Weather <city>")
    assert "/Weather <city>" in exc.value.message


def test_clean_name():
    assert clean_name("Mary Jane", "/X <n>") == "Mary Jane"
    assert clean_name("O'Connor-Smith", "/X <n>") == "O'Connor-Smith"
    for bad in ["", "1234", "<script>", "x" * 70]:
        with pytest.raises(CommandInputError):
            clean_name(bad, "/X <n>")


def test_clean_city():
    assert clean_city("Agra, India", "/X <c>") == "Agra, India"
    with pytest.raises(CommandInputError):
        clean_city("!!", "/X <c>")


def test_clean_username_instagram_rules():
    assert clean_username("torvalds", "/X <u>") == "torvalds"
    assert clean_username("@user.name_1", "/X <u>") == "user.name_1"
    for bad in ["", "bad user", "x" * 31]:
        with pytest.raises(CommandInputError):
            clean_username(bad, "/X <u>")


def test_clean_username_sherlock_rules():
    assert clean_username("user-name", "/X <u>", sherlock=True) == "user-name"
    with pytest.raises(CommandInputError):
        clean_username("x" * 41, "/X <u>", sherlock=True)


def test_clean_two_names():
    assert clean_two_names("Alex Sam", "/X <a> <b>") == ("Alex", "Sam")
    assert clean_two_names("Mary Jane Watson", "/X <a> <b>") == ("Mary Jane", "Watson")
    with pytest.raises(CommandInputError):
        clean_two_names("Solo", "/X <a> <b>")


def test_clean_query_limits():
    assert clean_query("what is this", "/X <q>") == "what is this"
    with pytest.raises(CommandInputError):
        clean_query("x" * 200, "/X <q>", max_length=100)
    with pytest.raises(CommandInputError):
        clean_query("!!!", "/X <q>")


def test_clean_phone_for_paused_command():
    assert clean_phone("+1 415 555 2671", "/Num <n>")
    with pytest.raises(CommandInputError):
        clean_phone("not-a-phone", "/Num <n>")


def test_clean_choice():
    mapping = {"male": "male", "m": "male", "female": "female"}
    assert clean_choice("", mapping, "/X", default="male") == "male"
    assert clean_choice("FEMALE", mapping, "/X", default="male") == "female"
    with pytest.raises(CommandInputError):
        clean_choice("banana", mapping, "/X", default="male")


def test_chunk_message_splits_on_lines():
    text = "\n".join(f"line {i} " + "y" * 50 for i in range(40))
    chunks = chunk_message(text, limit=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks).replace("\n", "") != ""


def test_chunk_message_keeps_short_text():
    assert chunk_message("hello") == ["hello"]
    assert chunk_message("") == []

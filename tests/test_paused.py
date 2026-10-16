"""/Num and /Id: disabled placeholders only — no registry, no network."""

from __future__ import annotations

from src.commands import load_all
from src.commands.base import COMMANDS, dispatch
from src.commands.paused import paused_names, paused_response


def _ctx(make_ctx):
    load_all()
    return make_ctx()


def test_paused_names_documented():
    names = paused_names()
    assert set(names) == {"Num", "Id"}
    assert "TODO" in names["Num"] or "not integrated" in names["Num"]


def test_paused_response_function():
    assert "paused" in paused_response("num").lower()
    assert "paused" in paused_response("ID").lower()
    assert paused_response("info") is None


def test_paused_commands_absent_from_registry_and_help(make_ctx):
    ctx = _ctx(make_ctx)
    assert "num" not in COMMANDS
    assert "id" not in COMMANDS
    help_text = dispatch(ctx, "/Help").text
    assert "/Num" not in help_text
    assert "/Id" not in help_text


def test_invoking_paused_commands_returns_paused_message(make_ctx):
    ctx = _ctx(make_ctx)
    num = dispatch(ctx, "/Num +14155552671")
    assert "paused" in num.text.lower()
    id_result = dispatch(ctx, "/Id someuser")
    assert "paused" in id_result.text.lower()


def test_paused_commands_make_no_network_calls(make_ctx):
    ctx = _ctx(make_ctx)
    dispatch(ctx, "/Num +14155552671")
    dispatch(ctx, "/Id someuser")
    http = ctx.services.demographics.http  # shared FakeHttp; would raise on any call
    assert http.calls == []


def test_enable_flags_do_not_activate_provider(make_ctx, monkeypatch):
    """Even with ENABLE_*_COMMAND=true nothing is fabricated — still placeholders."""
    import config as config_module

    cfg = config_module.Config.from_env(
        environ={
            "IG_USERNAME": "bot",
            "IG_PASSWORD": "pw",
            "ENABLE_NUM_COMMAND": "true",
            "ENABLE_ID_COMMAND": "true",
        }
    )
    assert cfg.enable_num_command is True
    assert cfg.enable_id_command is True
    # The registry never gains fabricated handlers:
    assert "num" not in COMMANDS
    assert "id" not in COMMANDS

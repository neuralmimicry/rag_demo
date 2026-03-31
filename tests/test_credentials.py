from __future__ import annotations

import builtins
import getpass

import credentials


def test_get_credentials_non_interactive_skips_prompts(monkeypatch) -> None:
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.delenv("JIRA_PASSWORD", raising=False)
    monkeypatch.setattr(credentials, "_can_prompt", lambda: False)
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(AssertionError("input called")))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": (_ for _ in ()).throw(AssertionError("getpass called")))

    assert credentials.get_credentials() == ("", "")


def test_get_credentials_interactive_handles_eof(monkeypatch) -> None:
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.delenv("JIRA_PASSWORD", raising=False)
    monkeypatch.setattr(credentials, "_can_prompt", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": (_ for _ in ()).throw(EOFError()))

    assert credentials.get_credentials() == ("", "")


def test_get_credentials_uses_instance_specific_env(monkeypatch) -> None:
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.delenv("JIRA_PASSWORD", raising=False)
    monkeypatch.setenv("JIRA_USERNAME_EXAMPLE_CO", "alice")
    monkeypatch.setenv("JIRA_PASSWORD_EXAMPLE_CO", "secret")

    assert credentials.get_credentials("Example Co") == ("alice", "secret")

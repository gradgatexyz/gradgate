import os

from gradgate import cli


def test_flags_name_the_traps():
    row = {"snipe_pct": 22.0, "exempt_n": 3, "farm_twins": 1, "creator_prior": 9, "creator_grads": 0, "creator_tax_bps": 100}
    assert cli.flags(row).plain == "snipers 22% · bundle 3 · farm ×2 · serial dev"
    assert cli.flags({"snipe_pct": 1.0}).plain == ""


def test_env_file_never_overrides_the_shell(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# comment\nGG_TEST_A=from-file\nGG_TEST_B='quoted'\n")
    monkeypatch.setenv("GG_TEST_A", "from-shell")
    monkeypatch.delenv("GG_TEST_B", raising=False)
    assert cli.load_env(str(f))
    assert os.environ["GG_TEST_A"] == "from-shell"
    assert os.environ["GG_TEST_B"] == "quoted"


def test_kill_blocks_buys_not_sells(tmp_path, monkeypatch):
    import types
    from gradgate import cli
    monkeypatch.setattr(cli, "ENGINE_DIR", str(tmp_path))
    open(tmp_path / "KILL", "w").write("stop")
    a = types.SimpleNamespace(yes=True)
    assert cli._confirm(a, "buy") is False
    assert cli._confirm(a, "sell", entry=False) is True

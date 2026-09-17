"""Every rule the engine reads is documented, and every documented rule exists: the docs cannot drift from the engine."""
import os
import re

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = open(os.path.join(ROOT, "docs", "STRATEGIES.md"), encoding="utf-8").read()


def documented() -> set[str]:
    return set(re.findall(r"^\| `([a-z_]+)`", DOC, re.M)) | set(re.findall(r"`(min_net|max_net)`", DOC))


def test_every_engine_rule_is_documented():
    keys = set(S.BASE["entry"]) | set(S.BASE["exit"]) | {"size", "cash", "quote"}
    assert keys - documented() == set()


def test_every_documented_rule_exists():
    keys = set(S.BASE["entry"]) | set(S.BASE["exit"]) | {"size", "cash", "quote"}
    assert documented() - keys == set()


def test_every_cli_command_is_documented():
    from gradgate import cli
    import inspect
    commands = set(re.findall(r'sub\.add_parser\("([a-z]+)"', inspect.getsource(cli.main)))
    doc = open(os.path.join(ROOT, "docs", "COMMANDS.md"), encoding="utf-8").read()
    assert {c for c in commands if f"gradgate {c}" not in doc} == set()

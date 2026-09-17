# contributing

thanks for looking. gradgate is small on purpose; changes that keep it readable land fastest.

## set up

```bash
./install.sh
.venv/bin/pip install -e ".[dev]"      # windows: .venv\Scripts\pip
.venv/bin/pytest -q
```

the terminal: `npm --prefix ui install`, then `npm --prefix ui run dev` with an engine running on 8765 (`.venv/bin/gradgate start --no-open`). commit `ui/dist` rebuilt with `npm --prefix ui run build` when you change the terminal — people run it without node.

## what a good change looks like

- **strategies are data.** a new rule goes into `BASE` in `engine/strategies.py` with an off position, into the checklist and the editor, and into `docs/STRATEGIES.md` — `tests/test_docs.py` fails until the docs match the engine.
- **live code is guarded.** anything that can sign or send goes through the gates in `strategies.Engine.live_ok` or plans first like `engine/trade.py`, and comes with a test that arms a trap in place of the sender (see `tests/test_live_safety.py`).
- **the key stays where it is.** a new file that reads `RH_PRIVATE_KEY` fails `test_only_known_places_read_the_key`; if it really needs to, say why in the test's comment.
- **numbers are measured.** a default, a threshold or a claim in the docs comes from the chain, with how you measured it.
- **comments say why**, not what. commit messages too.

## reporting

a bug: what you ran, what you expected, what happened, and `gradgate doctor` output (it shows no secrets). a security problem: privately, see [SECURITY.md](SECURITY.md).

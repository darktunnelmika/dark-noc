# Contributing

Thank you for helping improve DARK NOC.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r hub/requirements.txt -r agent/requirements.txt
python tests/smoke.py
python tests/agent_logic.py
node --check hub/static/app.js
for script in *.sh; do bash -n "$script"; done
```

## Pull requests

1. Create a focused branch from `main`.
2. Keep installation and upgrade paths backward-compatible.
3. Add a regression test for every bug fix.
4. Never add credentials, real server addresses, enrollment tokens or databases.
5. Update `CHANGELOG.md` for user-visible changes.
6. Explain operational risk and rollback behavior in the pull request.

Tunnel integrations must be explicit allowlisted plugins. Generic remote shell
jobs or arbitrary command execution through the Agent are not accepted.


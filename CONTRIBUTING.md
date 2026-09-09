# Contributing

Open an issue describing the problem or proposed behavior, then send a focused pull request. Include relevant tests for changes to account access, deployment, billing, data handling or provider behavior. Explain how you verified the change.

Use Python 3.12 and install `control/requirements.txt` plus `requirements-dev.txt` in a virtual environment. Run `python -m pytest -q`, `python scripts/check_public_tree.py`, and `gitleaks git --redact` before submitting.

Use synthetic data and reserved example domains. Never include API keys, account exports, customer data, connection codes, database snapshots, private business documents or production host details. Keep `.env` and generated state outside Git. Review your staged diff and Git author email; use GitHub’s private noreply email if you do not want a personal email published.

Contributions submitted for inclusion are licensed under Apache 2.0. Do not submit code you lack permission to contribute. Changes to the software license need maintainer agreement.

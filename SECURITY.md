# Security Policy

## The safety model
This project is designed to have **no security risk to your Fidelity account**:

- It never asks for, stores, or transmits your credentials, and never automates login.
- It uses **no Fidelity API** and **no third-party aggregator**. Data is obtained only when *you*
  paste a **read-only** script into your own already-authenticated browser session.
- The browser scripts make **zero network calls**, read no cookies/storage, click only Fidelity's
  own lot-expand buttons, and download a local CSV. This is enforced on every push/PR by
  `tests/test_browser_safety.py` (a static scan for banned network/credential/navigation APIs and
  obfuscated click forms).
- Your exported holdings never leave your machine and are never committed to git
  (`tests/test_data_safety.py` + `scripts/check_data_safety.py` fail CI if a real export or a
  Fidelity-style account identifier is tracked).

## Reporting a vulnerability
If you believe you have found a security issue — especially anything that would cause the browser
scripts to make a network call, touch credentials, perform a write action on the account, or leak
exported data — please report it privately:

1. Preferred: open a **GitHub Security Advisory** at
   <https://github.com/BlancosWay/Fidelity-portfolio-lab/security/advisories/new>.
2. Do **not** open a public issue for a suspected vulnerability.

Please include reproduction steps and the affected file(s). You can expect an initial response
within a few days. Thank you for helping keep users safe.

## Scope
In scope: the browser scripts (`scripts/browser/*.js`) and the analyzer (`scripts/analyze/*.py`).
Out of scope: Fidelity's own website, and any modified/forked copy of these scripts.

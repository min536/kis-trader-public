# Security and privacy

Do not put real credentials, account identifiers, operational logs or collected financial data in issues, pull requests, screenshots or example fixtures. Use synthetic data.

This code is not an authenticated hosted trading service. Keep operator dashboards on loopback or behind independently configured access control. Do not expose them directly to the internet.

Run secret scanning across the proposed public Git history, not only the current working tree. If a real secret was previously disclosed, revoke/rotate it; deleting its visible file is insufficient. Coordinate private reporting through the repository owner's private security reporting channel when one is enabled, rather than posting a secret publicly.

Reference: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository

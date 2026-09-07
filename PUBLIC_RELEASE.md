# Public snapshot boundaries

This snapshot keeps the trading and research implementation, tests, backtester, portable scripts, general architecture/design documentation and a synthetic dashboard demo. It starts a new Git history and does not import the operational repository's commits, branches, pull requests, issues, releases or attachments.

## Excluded material

- Credentials, token caches, operator-specific configuration, machine-local state and personal filesystem paths.
- Account identifiers and their old partial suffixes; balances, orders, performance logs and personal incident narratives.
- Collected price data, exchange master files, financial reports, research outputs and generated binary artifacts.
- Screenshots, conversations, Figma exports and a separate mini-app whose redistribution rights were not established.
- Imported agent packages without a reviewed license/provenance chain. The reviewed MIT UI UX Pro Max package is retained.

Excluded files may be mentioned in retained historical documents; they are not included in this distribution. Historical plans describe previous development states, not promises that every integration or operational configuration is provided.

## Release-only changes

- Account examples and bot names use synthetic, generic values.
- The configuration root is the root of this source package. A nested checkout cannot inherit an operational parent's configuration.
- The dashboard server defaults to loopback. The static HTML bootstrap is rebuilt without embedded operational state.
- Mixed-origin Reicon glyphs are replaced with 29 pinned Lucide SVGs. Existing variable/file names remain for compatibility; the actual shipped glyphs use Lucide/Feather licensing.
- Tests use synthetic account context instead of requiring local account files. No execution logic, strategy thresholds, market-session checks or process locks were removed.
- The checked-in regular-session profile is an empty public example; supply your own reviewed configuration locally.

## Verification scope

Content scanners and manifest verification reduce accidental disclosure; they cannot prove that every unknown secret or ownership claim has been discovered. Copyright notices and vendor references are retained. No broker calls or actual trading sessions are part of release verification.

Publishing source does not grant permission to redistribute broker/exchange data or operate a financial service. External API connections remain subject to provider terms and account eligibility. No provider assets or downloaded datasets are licensed by this project.

The project's own code and documentation are provided under the [MIT License](LICENSE). Third-party components retain their own licenses and notices, as described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the project license does not relicense those components or grant rights to external data or services.

For a future public upload, use this independent snapshot. Changing the original private repository's visibility would expose additional history and collaboration material outside this audit.

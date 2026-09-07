# Third-party notices

The project's own code and documentation use the [MIT License](LICENSE). The third-party components identified below retain their existing licenses and copyright notices; the root license does not replace them.

## Lucide / Feather icons

The dashboard's 29 selected glyphs come from Lucide 0.468.0, commit `f12b0de177fbc2a6795e99be065887e72b237123`.

- Source: https://github.com/lucide-icons/lucide/tree/f12b0de177fbc2a6795e99be065887e72b237123/icons
- Complete upstream notice: [LUCIDE_LICENSE.txt](workspace/claude-design/kis-trader-v2/project/LUCIDE_LICENSE.txt), including the ISC license and the MIT notice for Feather-derived icons.
- Modification: SVG inner elements are wrapped with stroke attributes and exposed under the dashboard's existing global mapping. Names in that mapping are compatibility aliases, not claims about the original icon vendor.
- Reicon glyphs and Figma-exported assets are not shipped in this snapshot.

## UI UX Pro Max

The optional `.codex/skills/ui-ux-pro-max/` package is retained under MIT. Copyright (c) 2024 Next Level Builder. The complete license is [included here](.codex/third_party/ui-ux-pro-max/LICENSE).

Upstream: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill

Local source history records that this package was vendored. This snapshot does not claim that local modifications are an unchanged current upstream release.

## Libraries installed by the user

Runtime/development packages are declared in `requirements.txt`, `requirements-dev.txt` and `constraints.txt`; their source distributions and installed environments are not bundled. Their own licenses apply. React 18.3.1, React DOM 18.3.1 and Babel standalone 7.29.0 are referenced through CDN URLs by the demo HTML; those runtimes are not copied into this repository.

## External API references

KIS, Toss Securities and DART names and endpoints identify integrations. No broker/exchange price database, account data, master archive, logo or third-party API sample repository is bundled. The official KIS sample repository did not expose a root license file during this audit; public visibility alone must not be treated as a blanket redistribution license for its code.

The local source was checked for long exact code sequences against the locally available KIS sample tree, with no matches at the stated eight-line / 300-character threshold. This heuristic is supporting evidence, not a complete copyright provenance proof.

API documentation: https://apiportal.koreainvestment.com/apiservice

Official KIS sample repository: https://github.com/koreainvestment/open-trading-api

## Quantitative research reference

`backtester/analytics/robust_risk.py` records GS Quant as a contract/reference source and describes its implementation as independent. No GS Quant package or Marquee data is bundled. Its research reference is documented in `docs/gs_quant_adoption_roadmap_20260902.md`.

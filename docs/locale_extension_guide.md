# Locale Extension Guide

Korean (`ko_KR`) is the reference implementation. All country-specific logic
lives in configs so new locales don't touch generation code.

## Steps to add a locale

1. **Locale config** — copy `configs/locales/ko_KR.yaml` to `<locale>.yaml`:
   currency, adulthood/retirement ages, school ages, housing contract types
   (e.g. drop jeonse for the US), financial products, banking terms,
   dialogue style, and `value_pools` (salary days, rent amounts, addresses,
   employers — all fictional).
2. **Prompts** — add `prompts/dialogue/generate_banking_session_<lang>.md`
   (+ repair prompt). The generator currently loads the ko prompt; parametrize
   the filename by `locale.language` when adding the second real locale.
3. **Event registry** — labels/cues are Korean-only today
   (`label_ko`, `discriminative_cues_ko`). Add `label_<lang>` /
   `discriminative_cues_<lang>` fields and select by locale in
   `fsm/registry.py`. Guards/lifecycle/rates are locale-neutral, but review
   age guards (e.g. legal adulthood) per country.
4. **Persona source** — implement an adapter in `persona/` for the new
   persona dataset, emitting `NormalizedPersona` (the rest of the pipeline is
   source-agnostic).
5. **Housing/product semantics** — jeonse-specific logic is confined to
   locale config + event params; map to local equivalents (deposit lease →
   security deposit, 전세대출 → renters insurance/loan analogues) or disable
   those events via `active: false`.
6. Run `make test` and a smoke pipeline with `--locale <locale>`.

`configs/locales/en_US.yaml` exists as a minimal template; en_US generation
is intentionally not implemented yet.

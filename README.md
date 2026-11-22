# Greenbank rainfall outlook — monitoring proposal (inactive)

The live dashboard was previously published at:

<https://greenbank-rainfall-outlook.mberka.chatgpt.site/?view=1&tab=outlook>

**The existing `index.html` in this repository is an October 2026 static export,
not a verified deployment source for the live Site.** The forecast snapshot
embedded in that file was captured on 4 October 2026; its forecast horizon ends
on 18 October and its Weather Underground observation history stops on
30 September. Do not treat it as current or publish it over the Site.

## Data lineage established from the October export

- Weather observations: Weather Underground personal station **IBRISB6274**,
  reportedly supplied by the Ecowitt station/gateway. Upload health and actual
  Ecowitt connectivity have **not** been independently verified.
- Precipitation forecasts: Open-Meteo ensemble API, ECMWF IFS, ECMWF AIFS and
  GFS seamless. The October embedded dataset contains those three models.
- The exported file includes a **broken** source-code link to
  `mberka-ship-it/fuel-report/scripts/weather`; that directory is absent. The
  `fuel-report` repository's active scheduled job concerns **fuel security**,
  not weather. Do not reuse or modify that workflow.
- `greenbank-weather` had no GitHub Actions runs and reported GitHub Pages
  disabled when checked on 22 November 2026.

## What this review branch proposes

1. `scripts/check_greenbank.py`: a **read-only** public-site checker (Python
   standard library only). It validates the embedded status/observations/models
   and archive, including forecast capture age (maximum 36 hours), station
   history (maximum two local calendar days old), forecast horizon (minimum
   13 days ahead), date/value integrity and the three expected forecast models.
2. `tests/test_check_greenbank.py`: local synthetic fixtures checking passing,
   stale, malformed, incomplete and mismatched states.
3. `.github/workflows/greenbank-monitor.yml`: a **deliberately disabled** draft
   workflow. It has no active schedule; its job has `if: ${{ false }}`. Even a
   manual dispatch would skip execution. A proposed 08:00 AEST schedule is shown
   only in comments. It will not run until the owner separately approves both
   enabling the job and scheduling it on the default branch.

No code in this proposal deploys files, writes to upstream services, changes
GitHub Pages, creates tasks, or refreshes the Site. Workflow permissions are
`contents: read` only.

## Offline validation (safe to run)

```sh
python -m unittest discover -s tests -v
python scripts/check_greenbank.py --file index.html \
  --now 2026-11-22T08:00:00+10:00
```

The second command **should fail** with a stale-data result: it checks the
archive's dated content rather than declaring the historical export healthy.

When separately approved, the checker can inspect the published URL with:

```sh
python scripts/check_greenbank.py
```

Exit codes: `0` = PASS; `1` = verifiable data failure; `2` = UNVERIFIABLE
(e.g. the published Site is inaccessible, uses a different HTML data format,
requires authentication, or has malformed metadata). An **UNVERIFIABLE** result
is not evidence of a station outage. JSON output includes only statuses and
summary metadata, never the downloaded page body.

An optional `--expected-forecast-id ID` supports **read-only** post-publication
verification after a separately authorised refresh. It does not refresh or
publish anything. The published HTML SHA-256 is included for comparisons when
there is a trusted expected build; it is not independently authenticated.

## Outstanding dependencies (do not assume they are resolved)

- Confirm which editable Site project publishes the current live URL and what
  machine-readable status it exposes; its format may differ from the October
  export. The live Site was not accessible from this investigation environment.
- Verify upstream station uploads and the Weather Underground reporting lag
  separately before attributing staleness to the Ecowitt hardware.
- Discover and authorise the real publishing/rebuild endpoint before proposing
  an on-demand refresh. This branch does **not** include auto-repair.
- Before activation, validate the checker against the actual current Site,
  review false-positive thresholds, arrange notification delivery and obtain
  explicit approval for any merge, schedule, Site edit, publication or other
  automation configuration.

# MMA position evidence: data audit and supported estimates

Reviewed October 29, 2026. The implementation supplies no fixed “mount = X% win” table. `backend/position_evidence.py` can estimate descriptive conditional rates from a properly sampled licensed MMA observation cohort; the repository currently has **zero eligible exact-position observations**. Post-fight statistics and finishing-position counts do not change that count.

## What the primary sources actually contain

| Source | Verified content and rights | Decision |
|---|---|---|
| [Sportradar UFC Fight Stats](https://docs.sportradar.com/ufc/stream-endpoints-websocket/fight-stats) | Per-fighter cumulative snapshots include `TIP.backControlTime`, `guardControlTime`, `halfGuardControlTime`, `mountControlTime`, `sideControlTime` and other durations. Packets replace the previous full stats snapshot; `timestamp` is when the packet was generated. Access requires an appropriate commercial feed agreement. | Suitable licensed **cumulative positional-exposure covariates** once contracted/recorded. Differences of cumulative counters do not prove the current instantaneous position, the exact transition wall time, or an unconditional win probability. Do not infer mount from ground strikes. |
| [Data Bank MMA / Figshare DOI 10.6084/m9.figshare.31069918.v1](https://figshare.com/articles/dataset/Data_Bank_MMA/31069918) | The [publisher-linked study](https://doi.org/10.3390/app16042034) codes finishing techniques and final positional contexts. The Figshare API reports CC BY 4.0. The actual 443,764-byte XLSX was inspected in memory: columns include winner, loser, final position, finish round and total duration, but no continuous or checkpoint position-exposure sequence. | Reusable with attribution for **descriptive finish taxonomy**, not a direct predictor. Winner-first columns and final position are explicit leakage hazards. A high share of submissions finishing from back control estimates a distribution *among finishes*, not probability of winning conditional on currently controlling the back. No rates from this workbook are loaded into trading. |
| [Bello et al., 2019, Ending MMA Combat](https://pmc.ncbi.nlm.nih.gov/articles/PMC6714373/) | The article compares grappling-action rates across outcome groups using 304 rounds from 58 UFC events in 2012–2014; five-round fights, draws and no contests were excluded. The primary article states CC BY-NC-ND 3.0, which differs from some aggregators' simpler license labels. | Useful technique definitions and hypotheses. Aggregate outcome-group tables lack an available timestamped per-bout exposure cohort for this estimator. Do not reverse them into live conditional probabilities, and do not assume unrestricted commercial data reuse. |
| [ViCoS Brazilian Jiu-Jitsu Positions Dataset](https://www2.vicos.si/resources/jiujitsu/) | The lab provides 120,279 labeled images from six sparring sequences, with exact guard/side-control/mount/back/turtle classes and fighter roles. It is CC BY-NC-SA 4.0. It describes BJJ sparring, not MMA bouts with aligned win labels. Back labels need not guarantee hooks. | A position-recognition research resource with a substantial domain and rights mismatch for trading. It is **not** 120,279 independent fights and supplies no MMA positional win denominator. Excluded from MMA outcome estimation. |
| [Mendeley MMA Fighter Pose Dataset v2](https://data.mendeley.com/datasets/c456bnk8bm/2) | Its data card describes 5,109 images from 20 UFC fights, 17 keypoints per fighter, CC BY-NC-SA 4.0, and explicitly excludes ground grappling/cage clinch. | Potential stand-up pose-recognition research only. No mount/back/guard win-probability labels or full exposure denominator. No footage or pretrained assets were downloaded. |
| [Maximilianb1/mma-fight-analyzer](https://github.com/Maximilianb1/mma-fight-analyzer) | The authors' repo describes 11 fights segmented into five-second clips, broad phase/pressure labels, fight-level holdout, and explicit academic-use limitations. Its “grappling/ground work” class is not an exact position. A broad permissive data license was not verified. | Read for annotation and identity-abstention ideas only. Do not copy unlicensed code/data or treat frames as independent outcome observations. |

The Figshare record was verified with the primary [article API](https://api.figshare.com/v2/articles/31069918), and its linked file is [Dados MMA planilha Final.xlsx](https://ndownloader.figshare.com/files/61022287), file ID `61022287`, supplied/computed MD5 `286b3bbc62ed1c4b8b2a11c9aae2565c`. This audit inspected the file; it did not import its post-fight labels as state features. Publication and data licenses also do not automatically settle underlying broadcast-footage rights.

## Important withdrawn-result finding

[FightTracker, arXiv 2312.11067](https://arxiv.org/abs/2312.11067) was withdrawn by its author in April 2026. The withdrawal comment says longer-period analyses found substantially lower performance and the original short-period profit result could mislead. The initial profit claim must not be presented as evidence that this bot can trade profitably. Its round-statistics approach can motivate a hypothesis, but no old ROI or fitted model is inherited here.

## Why the denominator matters

For a legitimate checkpoint estimate, the numerator is the number of independently observed bouts where the specified fighter role eventually wins, and the denominator is **all eligible bouts observed in that position at the same predeclared checkpoint**, including losses. Neither “finishes from mount / all finishes” nor “successful mount submissions / mount submissions” has that denominator.

Even the correct denominator produces an association. Skill, score, prior odds, damage, round/time, division and opponent quality influence both positional access and winning. A raw rate cannot replace a calibrated pre-fight/in-fight model, and changing a fighter's position does not imply that rate is the causal effect. Bouts must be split by card/date; repeated frames, near-duplicate source mirrors and multiple judges cannot become independent bouts. Future modeling should add frozen prior probability, score/damage and time covariates using licensed point-in-time data and test beyond the contemporaneous market after costs.

## Implemented canonical contract

```python
position_evidence(
    observations, outcomes, sources, protocol,
    as_of_ms=..., min_bouts=30, min_cards=5, allow_mock=False,
)
```

All records are typed Pydantic models with extra fields rejected:

- `PositionSource`: `source_id`, `name`, `url`, `license_name`, `reuse_authorized`, `permitted_use` (`research_only`, `commercial_allowed`, `unknown`), `sport`, `data_kind` (`real`/`mock`), `coverage` (`full_exposure`, `fixed_checkpoint`, `finish_only`, `unknown`), `provisional`.
- `PositionSamplingProtocol`: `protocol_id`, `description`, `created_at_ms`, `round`, `clock_remaining_s`, `sampling_rule="fixed_fight_clock"`, optional `tolerance_s` (default 0, maximum 30), `perspective` (`controller` or `top_fighter`), `min_confidence` (default 0.95).
- `PositionObservation`: `observation_id`, `source_id`, `protocol_id`, `bout_id`, `card_id`, `fighter_a_id`, `fighter_b_id`, `round`, `clock_remaining_s`, `observed_at_ms`, `available_at_ms`, optional `event_wall_ms` and `wall_time_uncertainty_ms`, `time_basis`, `position`, `top_fighter`, `controller`, `confidence`, `review_status`.
- `BoutOutcome`: `result_id`, `source_id`, `bout_id`, `card_id`, `fighter_a_id`, `fighter_b_id`, `winner`, `available_at_ms`, `finalized`. Outcomes are never accepted as position-observation features.

`observed_at_ms` is when the observer/annotator obtained the annotation. `available_at_ms` cannot precede it. A historical video annotation published today does not become “available” on the old fight date. Preserve a known physical event wall time separately; leave it null when unproven. `time_basis` distinguishes physical-event, provider-generated, broadcast-observation and historical-video timing. Fight clock remains `(round, seconds remaining)`, so clock pauses and broadcast delay are not mistaken for UTC offsets.

The taxonomy distinguishes `closed_guard`, `open_guard`, `half_guard`, `fifty_fifty_guard`, `side_control`, `north_south`, `knee_on_belly`, `mount`, `back_control`, `turtle`, `standing`, `clinch`, `takedown`, `scramble`, `ground_unknown` and `unknown`. A seated back-control fighter is not necessarily physically on top. Guard player, top fighter, controller and eventual winner are separate fields; unknown roles must remain unknown.

## Sampling and output safeguards

1. Exclude mocks unless the explicit test switch is supplied. Exclude non-MMA cohorts, unknown/provisional rights, provisional annotations, unconfirmed labels, low confidence and finish-only/unknown observation coverage.
2. The protocol must be recorded before observations are obtained. This is a procedural audit guard, not proof against deliberate post-hoc registration. Historical annotation studies should blind annotators to outcomes and retain the registration and selection manifest.
3. Select one observation per bout **before grouping by position or joining results**: closest earlier-or-exact fight-clock snapshot within the predeclared window, in the same round. A later frame cannot fill an earlier checkpoint. Conflicting equal-time state annotations exclude the bout; identical duplicate IDs are deduplicated and conflicting duplicates fail.
4. Join only finalized, correctly fighter-mapped outcomes available by `as_of_ms`; exclude draws/NC rather than quietly converting them to binary labels. Later result corrections are only used after their own availability time.
5. Publish a conditional estimate only with at least 30 distinct bouts and five cards by default. Return counts and `insufficient_data` with `win_probability=null` below threshold. Empty inputs return no percentage table.
6. Estimates use Beta(1,1) smoothing, a 95% IID posterior diagnostic and a seeded card-cluster bootstrap interval. Intervals describe sampling uncertainty under their assumptions, not annotation bias, distribution shift, causality or trading profitability. More data from one card or thousands of frames cannot replace independent events.

The endpoint/report always returns `approved_for_trading=false`. The research module does not change any live prior, bypass a risk gate, enable real execution, or imply vendor entitlements. The next data acquisition step is a licensed whole-bout feed or independently reviewed, rights-cleared full-bout annotations at preregistered checkpoints, with losses, cohort coverage and missingness retained.

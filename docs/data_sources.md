# Historical MMA observations and research baselines

Reviewed October 29, 2026. The acquisition and model code use real observations; synthetic tests are confined to `tests/test_historical.py`. The models produced here are research artifacts and cannot be loaded as the application's market-offset live models.

## Acquired source and attribution

[Noah Bair's UFC Data v0.1.1](https://github.com/nlbair/ufc-data) publishes an explicit [CC BY 4.0 data license](https://github.com/nlbair/ufc-data/blob/main/LICENSE). This repository acquires immutable bytes from commit `a9c65430b3c5cb6e6351940f36e03b2adeb908d0`. The downloader verifies reviewed SHA-256 checksums before using cached or downloaded files. Upstream changes require an intentional source review and new pin; they are never silently accepted.

Credit: Noah Bair, *UFC Data: scorecards, round statistics, and betting odds, 2013 to 2026*, version 0.1.1, [DOI](https://doi.org/10.5281/zenodo.23458636). Underlying sources credited by the publisher are UFCStats, MMA Decisions, BestFightOdds, historical UFC rankings archives, and the UFCalendar rankings API. This work transforms the source into canonical fighter ordering, embargoed lagged features and majority-judge round labels. It is independent of and not endorsed by UFC. The publisher's license does not supply a licensed live UFC feed or waive underlying service-access terms.

Actual imported counts:

| File | Observations |
|---|---:|
| `ufc_all_fights_wide.csv` | 6,743 unique bouts, February 16, 2013–October 10, 2026 |
| `ufc_judge_rounds.csv` | 29,937 judge-round rows |
| Pre-fight derived samples | 3,828 bouts with at least two earlier observed appearances for each fighter |
| Completed-round scoring samples | 9,866 distinct rounds with three binary judge scores |

The original files, license, source documentation, citation and download manifest are retained under ignored `data/research/nlbair/`. Derived observations and model JSON files are under `data/research/nlbair/derived/`. They are excluded from source commits. `docs/data-training-report.json` records provenance, actual exclusion counts, split sizes and computed evaluation metrics without copying a dataset into the public repository.

Run acquisition and training from this repository root after installing requirements:

```powershell
.venv/Scripts/python scripts/acquire_mma_data.py --train
```

On macOS/Linux use `.venv/bin/python`. Acquisition without `--train` validates and builds the samples without fitting weights. There is no need to scrape the original websites for this pinned release.

## What the pre-fight model actually learns

The source warns that fighter 1 is often the winner. The importer therefore uses lexically sorted stable fighter IDs to choose A/B and signs both features and labels accordingly. Training additionally includes both fighter orientations and has no intercept. The source's raw corner/listing position is never a feature.

Features are prior Elo, observed win rate, observed experience, layoff, significant striking for/against, takedowns, submission attempts, control share, knockdowns, and significant-strike rates at distance, in clinch and on the ground. These statistics are computed exclusively from earlier bouts. There is a seven-day calendar embargo, and all bouts on a date share the same pre-card history. Missing statistics contribute no zero-valued exposure. Fighter physicals, present-day career summaries, current-fight totals, method/finish time, judges' outcomes and unverified sportsbook odds are excluded from pre-fight inputs.

The observed history begins in 2013, so "experience" means appearances in this observed source window, not a fighter's full career. Draws, no contests and overturned results still update appearance count, last appearance and observed statistical exposure; binary W/L rate and Elo updates use only binary outcomes. Requiring two known prior appearances excludes 2,790 bouts; another 125 have nonbinary/overturned target outcomes. This sample favors fighters with an established observed history and should not be generalized to debuts without a separate cold-start model.

Elo is a fixed K=24, scale-400 research baseline, updated in chronological card groups. The statistical model is a symmetric regularized logistic baseline with scaling fitted only on the training partition. A scalar temperature is fitted only on the later calibration partition. The final test period is never used to choose coefficients, regularization, temperature or features.

The dataset is a current historical snapshot. It lacks observed first-publication or original receive times and may include later result/statistic corrections. A seven-day embargo is an explicit conservative reconstruction assumption, **not proof** of point-in-time availability. These observations therefore cannot be passed off as the existing platform's timestamped forward feature records. No market probability of 0.5 is fabricated to satisfy the market-offset model's input schema.

## Actual retrospective evaluation

Fixed partitions were chosen before fitting: train through 2022, calibrate during 2023, test from 2024 onward, with boundary embargoes. Pre-fight counts are 2,521 / 315 / 992. The 992-bout test set spans 119 cards from January 13, 2024 to October 10, 2026. Its observed log loss is **0.6560**, versus **0.6828** for the Elo baseline and **0.6931** for a 50% prediction. Accuracy is **62.30%**. Full precision, Brier, equal-width calibration error and per-year metrics are in the generated report. These replace the earlier implementation's figures after a code review corrected missing nonbinary appearance history; the change was a data-construction bug fix, not a test-set hyperparameter search.

These are retrospective predictive checks on the selected historical sample. They do not compare against executable Kalshi prices, include transaction costs, establish an in-fight information lead, or demonstrate a profitable trading strategy. A post-hoc sample source with unproven original publication times cannot establish forward performance even when its chronological feature construction excludes same-bout information.

## Completed-round model and position detail

The second baseline predicts **the majority of the three judges' completed-round labels**, conditional on completed-round statistics. It does not predict the fight winner from an instantaneous grappling position. All three judge rows are reduced to one round sample; two judges do not become two independent victories. Incomplete judge panels and nonbinary score rows are excluded. Unknown judge identities remain distinct published card slots rather than invented identities.

Round features are knockdowns, significant strikes, takedowns, submission attempts, control seconds, and distance/clinch/ground significant-strike differences. Train/calibration/test counts are 6,780 / 793 / 2,293 rounds, with whole cards retained together. On the 2,293 test rounds from 721 decision bouts, log loss is **0.3646** and accuracy **84.39%**. This is a different, easier target than pre-fight winner prediction. It is conditional on bouts going to a decision and on having three available judge labels; it does not validate performance on bouts that finish early. Round rows within a card/bout are correlated, so these are descriptive point estimates rather than independent-sample confidence claims.

Distance/clinch/ground are **strike contexts**. This source does not contain occupancy time or timestamped transitions for mount, side control, closed/half guard, back control or submission-lock depth. Consequently this extension does not invent a "back control = X% to win" table, turn finishing-position frequencies into state-conditioned win probabilities, or treat ground strikes as proof of mount. A licensed positional feed or properly licensed annotated video history is needed for that model. Position state should also be conditioned on fighter skill, score, remaining time, damage, top/bottom orientation, and observation delay.

## Other original repositories and studies reviewed

| Source | Useful direction | Use here |
|---|---|---|
| [gdsakelaris/the-fight-model](https://github.com/gdsakelaris/the-fight-model) | Chronological running fighter state, phase-specific ratings, finish-method modeling; repository publishes an MIT license | Design/reference only. Its UFC statistics overlap the acquired source, so appending the rows would duplicate fights. Its published results are not adopted as this platform's results. |
| [DanMcInerney/mma-ai](https://github.com/DanMcInerney/mma-ai) | UFCStats round columns, timestamp-aware database pipeline and multi-layer feature engineering | Reference only. No external bootstrap/model pickle is executed. Data/model redistribution terms and provenance need separate review before any import. |
| [FightTracker, arXiv:2312.11067](https://arxiv.org/abs/2312.11067) | Completed-round judging and late-fight outcome models | The author withdrew the preprint in April 2026 because a much longer analysis showed substantially lower performance than its original short-period profit claim. The original ROI claim is not used as evidence of a trading edge. |

The reviewed public sources supply post-fight/round statistics. They do not establish a 12-second Kalshi reaction delay. That hypothesis requires synchronized fight-event, provider-availability, market-source and local-receive timestamps captured from the same bouts.

## Test coverage and remaining validation

`tests/test_historical.py` checks independence from same-bout/future totals, listing-order invariance, duplicate rejection, the date embargo, nonbinary appearance history, source hash integrity, strict judge orientation, judge-to-round aggregation, exact probability symmetry, whole-bout temporal partitions and research-only artifact gates. Fitted artifact input hashes cover training and calibration only; test-input hashes are separate in the report, and changing test labels leaves the fitted artifact unchanged. The real dataset acquisition/training command has been executed; synthetic unit fixtures are not used for the reported historical metrics.

Before the model can inform live execution, collect licensed forward features, independent timestamped market books, settlement values and measured slippage/fees; verify out-of-sample calibration against the contemporaneous market; and retain the paper/human-approval gates. The present historical artifact remains explicitly `research_only=true` and `approved_for_trading=false`.

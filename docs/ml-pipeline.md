# Experimental pre-fight comparison and fixture inference

Run `python scripts/train_models.py` from this checkout using the installed requirements. The existing local interpreter is `../ufc-research/.venv/Scripts/python`. This command verifies all six pinned source hashes in `data/research/nlbair`, rebuilds inexpensive lagged samples, and uses a content/config/code/version cache. It never downloads data or refits `artifacts/research/prefight.json`. Matching subsequent runs return the saved comparison. `--force` explicitly repeats the experiment.

Source: Noah Bair, *UFC Data*, version 0.1.1, CC BY 4.0; repository `nlbair/ufc-data`, commit `a9c65430b3c5cb6e6351940f36e03b2adeb908d0`. Exact attribution, byte hashes, partition fingerprints, software versions and configuration accompany the report and portable JSON artifact. No pickle or executable model serialization is loaded.

The fixed candidate registry contains Elo, sklearn logistic regression with C=0.1 and C=1, and 60-tree gradient boosting with depth 1 and depth 2. Logistic fits use training-only RMS scaling, complement augmentation and no intercept. Trees use complement augmentation and symmetric logits to ensure swapping fighters complements the probability. No age, reach, odds, rankings or unavailable demographic values are inferred.

Training contains 2,521 bouts through December 2022, subject to a seven-day label availability embargo. Candidate selection uses only uncalibrated January–June 2023 validation log loss (147 bouts). One temperature is fitted on July–December 2023 calibration (156 bouts). Boundary rows are excluded. Calendar dates/cards never straddle partitions. Expanding-window diagnostics train through each preceding year and evaluate 2020, 2021 and 2022 with fixed hyperparameters and no fold calibration. They do not select candidates.

The frozen research baseline is benchmark-only: its prior calibration used all 2023, overlapping the new selection period. Refitting it or allowing it to compete on that period would misstate the comparison. The existing aggregate 2024–2026 test results were already viewed by this project. The 992-bout holdout is explicitly **reused**, not project-level untouched evidence. No candidate choices were made from its new scores.

Actual scores from the bounded run:

| Model | Validation log loss | Reused holdout log loss | Brier | Accuracy |
| --- | ---: | ---: | ---: | ---: |
| Elo | 0.697338 | 0.682774 | 0.244850 | 55.34% |
| Logistic C=0.1, before calibration | 0.675717 | 0.657476 | 0.232715 | 61.79% |
| Logistic C=1 | 0.675907 | 0.657701 | 0.232833 | 61.49% |
| Boosted trees depth 1 | 0.696948 | 0.676504 | 0.241750 | 57.86% |
| Boosted trees depth 2 | 0.686776 | 0.670907 | 0.239032 | 58.77% |
| Selected C=0.1 after calibration | — | 0.657287 | 0.232633 | 61.79% |
| Frozen prior research logistic | Excluded | 0.655993 | 0.231977 | 62.30% |

The selected model's ECE is 0.024301 using ten fixed equal-width probability bins. Full bin counts, observed fractions, all candidate ECE values and walk-forward results appear in `docs/model-comparison.json`. The new candidate does not improve the frozen baseline's reused-holdout log loss. Small validation/calibration sets and repeated project exposure limit conclusions. No market benchmark is available; no market prices, returns or trading edge are fabricated.

`PredictionService(root)` loads and verifies cached source bytes and `artifacts/ml/selected.json` once. It never trains or accesses the network. `fighters()` returns `{id, name, aliases}` from actual source identities/names. `status()` reports availability, model version, calibration/training availability cutoff and restrictions. Initialization failures report unavailable and predictions fail closed.

`predict(fighter_a_id, fighter_b_id, scheduled_ms, as_of_ms)` requires exact distinct canonical IDs, UTC integer milliseconds, a schedule at/after the cutoff, a cutoff at/after model calibration availability, and at least two released prior bouts per fighter. It reconstructs pre-card Elo and lagged statistics using only rows whose calendar date plus seven days is at/before the cutoff. Scheduled date is used only for layoff duration. Future and same-bout statistics/outcomes cannot enter history. No fixture or scheduled event time is invented. Returned fields include `probability_a`, `model_version`, `features_asof_ms`, `trained_through_ms`, exact features, history counts, and limitations.

All output is permanently labeled retrospective availability reconstruction, `research_only=True`, `approved_for_trading=False`, and `eligible_for_live=False`. Seven-day source availability is assumed, not observed, and the snapshot can contain later corrections. This interface supports strictly opt-in experimental PAPER analysis only and cannot register or approve an execution/live model.

Focused verification: `python -m pytest tests/test_ml_pipeline.py -q --basetemp artifacts/ml/.pytest_tmp`. Tests compare upcoming feature reconstruction against the canonical training builder, exclude unreleased/future results, verify exact fighter complements and JSON tree predictions against sklearn, reject identity/date/history errors and artifact tampering, and exercise service provenance and unavailable-artifact behavior.

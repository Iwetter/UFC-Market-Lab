# Fitted research baselines

These JSON files contain actual fitted coefficients produced by `scripts/acquire_mma_data.py --train --artifact-output artifacts/research` from the pinned source and code described in `docs/data_sources.md` and `docs/data-training-report.json`.

Source attribution: Noah Bair, *UFC Data: scorecards, round statistics, and betting odds, 2013 to 2026*, version 0.1.1, [nlbair/ufc-data](https://github.com/nlbair/ufc-data), commit `a9c65430b3c5cb6e6351940f36e03b2adeb908d0`, CC BY 4.0. The dataset combines UFCStats, MMA Decisions, BestFightOdds and historical ranking sources. Our lagged feature reconstruction, orientation normalization, filtering and fitted coefficients are derived changes; upstream does not endorse this platform.

`prefight.json` predicts the historical binary bout outcome from earlier observed bouts. `round_scoring.json` predicts a completed decision round's majority judge score from that round's statistics. Neither is an executable-market residual model, an instantaneous position model, or a trading authorization. Their artifacts explicitly declare `research_only=true` and `approved_for_trading=false`; the live/paper trading model loader rejects their feature registry.

Train ≤2022, calibration 2023, held-out evaluation 2024–October 10, 2026. Original publication timestamps are unavailable. A seven-day date embargo is an assumption for retrospective reconstruction, not fabricated observed availability. No test labels enter the model artifact or its fitted-input hash. Raw acquired data remains in ignored local storage and is reproducibly downloaded by the acquisition script.

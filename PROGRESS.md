# Progress — October 29, 2026

Repo: Iwetter/UFC-Market-Lab; checkout is this directory. Base main: 3166554. Working branch: research/data-latency-2026-10-29. Draft PR: https://github.com/Iwetter/UFC-Market-Lab/pull/1 (not merged). Implementation commit: d1e18ff.

## Completed

- Pinned/hashed CC BY 4.0 historical acquisition: 6,743 bouts, 29,937 judge rows. Corrected nonbinary appearance history, canonical fighter ordering, strict dedup and embargo.
- 3,828 pre-fight samples; 9,866 majority-judge round samples. Frozen research-only artifacts: artifacts/research. Train through 2022, calibrate 2023, test 2024–Oct10 2026. Pre-fight test 992 bouts: LL 0.6560 vs Elo 0.6828, accuracy 62.30%. Full provenance/metrics: docs/data-training-report.json. No trading-edge result.
- Physical/broadcast timing study, quote/depth/lifecycle/confound guards, interval uncertainty, card bootstrap, negative leads and session/window bounds. Actual REST smoke: 26 markets / 104 book observations / zero paired fight events. Twelve seconds is unverified.
- Typed position evidence with one predeclared checkpoint/bout and uncertainty; no eligible public mount/back exposure cohort found. Finish-only tables rejected.
- Optional licensed Sportradar raw/TIP recorder with durable resume and secret guards; integrated same-journal collectors are opt-in. No credentials tested. Research API and dashboard are isolated from execution approvals.
- Always-on private Docker/PAPER deployment prepared; single-writer leases, health, restart/failure handling, atomic WAL backups. Docker/cloud execution unverified because Docker/server access is unavailable. No purchases.
- Daily continuation created: automation `ufc-market-lab-focused-development`, heartbeat attached to this existing chat, daily 05:15 America/New_York, starting Oct29. Do not create another. No main-session model switch tool available; user should select Sol Medium. Usage checked once this batch; ordinary usage was available, no credits/reset consumed.
- Added AGENTS/ROADMAP and cooperating development lease for non-overlapping bounded batches.

## Verification and immediate next task

Final integration: 110 Python tests + 2 frontend tests passed; TypeScript/Vite build, source secret scan and git diff checks passed. Compose YAML parses. The 30-minute dashboard analysis window runs in a separate bounded worker and retains prior metadata. Development leases prevent overlapping cooperating runs and recover expired runs. Browser verified actual historical report and that mock tags cannot support real latency.

GitHub handoff saved as draft PR #1. Next: inspect its CI results and resolve any demonstrated failure. Then highest priority is credentialed card recording and provider-packet validation once a real read-only key/license/server exists. Until then, improve collector/replay correctness with recorded public data and offline fixtures; don't retrain the same baseline. See ROADMAP.md.

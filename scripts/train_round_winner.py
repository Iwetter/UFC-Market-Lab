"""One cached, fixed-spec logistic experiment; no searches or startup training."""
import json
import sys
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression
from backend.domain import digest
from backend.historical import build_prefight_samples, read_csv, SOURCE_COMMIT
from backend.historical_models import metrics
from backend.ml_pipeline import verify_sources, seal, predict_portable
from backend.round_winner import FEATURES, TARGET, AVAILABILITY, checkpoint_features, round_observation, predict


def run(root=ROOT):
    path = root/'artifacts/ml/round-winner.json'
    if path.exists():
        print('Cached checkpoint artifact exists; no unchanged retraining.'); return
    verify_sources(root/'data/research/nlbair')
    fights = read_csv(root/'data/research/nlbair/data/ufc_all_fights_wide.csv')
    priors, _ = build_prefight_samples(fights)
    by_id = {r['fight_id']:r for r in fights}
    samples, excluded = [], 0
    for prior in priors:
        row = by_id[prior['bout_id']]
        scheduled = float(row['scheduled_rounds'] or 0)
        if scheduled not in (3,5): continue
        observations = []
        for n in range(1,int(scheduled)):
            # Selection says only that the fight is still in progress, never how long it lasts.
            if float(row['fight_duration_sec']) <= n*300: break
            observations.append(round_observation(row,n,prior['fighter_a_id']))
            try: features = checkpoint_features(prior['features'],observations,int(scheduled),n*300)
            except ValueError: excluded += 1; continue
            samples.append({**prior,'round':n,'features':features,'prior':prior['features']})
    groups = {k:[] for k in ('train','validation','calibration','test')}
    for r in samples:
        d = date.fromisoformat(r['event_date']); released = d+timedelta(days=7)
        if released <= date(2022,12,31): fold='train'
        elif date(2022,12,31) < d and released <= date(2023,6,30): fold='validation'
        elif date(2023,7,7) < d and released <= date(2023,12,31): fold='calibration'
        elif d > date(2024,1,7): fold='test'
        else: continue
        groups[fold].append(r)
    x = np.asarray([[r['features'][f] for f in FEATURES] for r in groups['train']])
    y = np.asarray([r['y'] for r in groups['train']])
    scale = np.maximum(np.sqrt(np.mean(x*x,axis=0)),1e-8)
    estimator = LogisticRegression(C=.1,fit_intercept=False,max_iter=500,random_state=1729)
    estimator.fit(np.r_[x/scale,-x/scale],np.r_[y,1-y])
    model = dict(kind='symmetric_logistic',target=TARGET,features=FEATURES,scale=scale.tolist(),
                 coef=estimator.coef_[0].tolist(),temperature=1.,C=.1,trained_through='2023-12-31',
                 source_commit=SOURCE_COMMIT,training_rows_sha256=digest(groups['train']),
                 calibration_rows_sha256=digest(groups['calibration']),
                 availability_basis=AVAILABILITY,research_only=True,eligible_for_live=False,approved_for_trading=False)
    model = seal(model)
    cal = groups['calibration']; logits = np.array([sum(predict(model,r['features'])[1].values()) for r in cal])
    labels = np.array([r['y'] for r in cal])
    def loss(t):
        z = logits/t
        return float(np.mean(np.logaddexp(0,z)-labels*z))
    model['temperature'] = float(minimize_scalar(loss,bounds=(.25,4),method='bounded').x)
    model = seal(model)
    report = {'target':TARGET,'spec_sha256':model['spec_sha256'],'source_commit':SOURCE_COMMIT,
              'excluded_missing_checkpoints':excluded,'sample_sha256':digest(samples),'folds':{},
              'limitations':['Reused historical holdout, not a new prospective lockbox.',
              'Conditional on surviving each checkpoint; multiple rows per bout are correlated.',
              'Round-end availability assumed retrospectively; no observed event/publication timestamps.',
              'No exchange benchmark or evidence of trading profit; no live coverage.',
              'Single prespecified C=.1 logistic fit; validation is diagnostic, temperature fit on calibration only.']}
    prefight = json.loads((root/'artifacts/ml/selected.json').read_text())
    for name,rows in groups.items():
        # Validation predates calibration: report its genuinely forward raw fit.
        evaluated = seal({**model,'temperature':1.}) if name=='validation' else model
        p = [predict(evaluated,r['features'])[0] for r in rows]; labels = [r['y'] for r in rows]
        result = {'checkpoints':len(rows),'bouts':len({r['bout_id'] for r in rows}),
                  'first_date':min(r['event_date'] for r in rows),'last_date':max(r['event_date'] for r in rows),
                  'round_model':metrics(p,labels), 'temperature':evaluated['temperature'],
                  'elo_baseline':metrics([r['elo_probability'] for r in rows],labels),
                  'equal_baseline':metrics([.5]*len(rows),labels),'calibration_bins':[], 'by_round':{}}
        # Selected artifact is legal only after its own training/calibration date.
        if name=='test': result['existing_prefight_baseline']=metrics([predict_portable(prefight,r['prior']) for r in rows],labels)
        for i in range(10):
            ix=[j for j,v in enumerate(p) if i/10<=v<(i+1)/10]
            result['calibration_bins'].append({'lower':i/10,'n':len(ix),'mean_probability':float(np.mean([p[j] for j in ix])) if ix else None,'observed_fraction':float(np.mean([labels[j] for j in ix])) if ix else None})
        for n in sorted({r['round'] for r in rows}):
            ix=[j for j,r in enumerate(rows) if r['round']==n]
            result['by_round'][n]=metrics([p[j] for j in ix],[labels[j] for j in ix])
        report['folds'][name]=result
    path.write_text(json.dumps(model,indent=2)+'\n')
    (root/'docs/round-winner-evaluation.json').write_text(json.dumps(report,indent=2)+'\n')
    cache=root/'data/research/nlbair/derived/round-winner-samples.json'
    cache.write_text(json.dumps(samples))
    print(json.dumps({k:v['round_model'] for k,v in report['folds'].items()},indent=2))


if __name__=='__main__': run()

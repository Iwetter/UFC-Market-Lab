"""Rerun one saved PAPER practice summary using cached data and audited inputs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.domain import Settings
from backend.practice import PracticeController


def run(original_path, output, manifest_path=None):
    original_path, output = Path(original_path).resolve(), Path(output).resolve()
    original = json.loads(original_path.read_text(encoding='utf-8'))
    manifest_path = Path(manifest_path) if manifest_path else original_path.parent/'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    output.mkdir(parents=True, exist_ok=False)
    controller = PracticeController(ROOT, output/'runs')
    policy = original.get('policy') or manifest.get('policy')
    if policy:
        controller.configure(policy)
    settings = original.get('settings') or manifest.get('settings') or {}
    # Retain economic settings, but never reuse the original account or enable live.
    def isolated_settings(**kwargs):
        return Settings(**(settings | kwargs | {'live_enabled':False}))
    try:
        with patch('backend.practice.Settings', isolated_settings):
            controller.start(original['fight']['id'])
        controller.control('pause')
        while controller.status != 'finished':
            controller.control('step')
        rerun = controller.snapshot()
        rerun_manifest = json.loads((controller.run_directory/'manifest.json').read_text(encoding='utf-8'))
        checks = {}
        for name, old, new in (
            ('source_commit', manifest.get('source_commit'), rerun_manifest['source_commit']),
            ('model_version', original.get('model_version'), rerun.get('model_version')),
            ('probability_a', original.get('probability_a'), rerun.get('probability_a')),
            ('quote_algorithm', manifest.get('quote_algorithm'), rerun_manifest['quote_algorithm']),
        ):
            checks[name] = {'original':old, 'rerun':new, 'match':old == new if old is not None else None}
        original_quotes = {d['virtual_time_ms']:(d['yes_bid'],d['yes_ask']) for d in original['history']}
        rerun_quotes = {d['virtual_time_ms']:(d['yes_bid'],d['yes_ask']) for d in rerun['history']}
        checks['decision_quote_path'] = {'match':original_quotes == rerun_quotes,
            'original_points':len(original_quotes),'rerun_points':len(rerun_quotes)}
        def brief(state):
            return {'run_id':state['run_id'], 'probability_a':state['probability_a'],
                'fills':len(state.get('fills', [])),
                'fill_actions':dict(Counter(f['action'] for f in state.get('fills', []))),
                'net_pnl':state['portfolio']['net_pnl'],
                'strategy':{k:state.get('strategy', {}).get(k) for k in
                    ('realized_trading_pnl','settlement_pnl','total_net_pnl','completed_cycles')}}
        report = {'original_path':str(original_path), 'fight':original['fight']['label'],
            'scope':'Comparison of the saved run identified above with current shared strategy.',
            'mode':'PAPER; synthetic-market experiment, not historical exchange returns',
            'input_checks':checks, 'same_inputs_verified':all(c['match'] is True for c in checks.values()),
            'policy_source':'saved policy' if policy else 'current defaults; original policy absent',
            'execution_settings_source':'saved settings; account path isolated' if settings else 'current defaults; original settings absent',
            'original':brief(original), 'rerun':brief(rerun),
            'legacy_policy_diagnosis':{
                'revision':'721a105',
                'sell_rule':'bid - slippage - model_probability - estimated_fee - probability_buffer >= min_exit_edge',
                'illustration':'At p=.85, defaults slippage=.01, buffer=.05 and exit edge=.02 require bid >= .93 + fee (about .94-.95). Synthetic maximum YES bid is .81. The legacy rule therefore cannot take a profitable YES exit while p remains .85.',
                'qualification':'Formula describes the legacy rule; the saved run and input checks above identify the actual comparison.'},
            'decision_summary':rerun['decision_summary'], 'journal_valid':controller.store.verify(),
            'rerun_summary':str(controller.run_directory/'summary.json')}
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (output/'decisions.json').write_text(json.dumps(rerun['history'], indent=2), encoding='utf-8')
        (output/'summary.json').write_text(json.dumps(rerun, indent=2), encoding='utf-8')
        return report
    finally:
        controller.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', required=True)
    parser.add_argument('--output', required=True, help='New output directory; must not exist')
    parser.add_argument('--manifest', help='Original manifest when the summary was exported elsewhere')
    args = parser.parse_args()
    print(json.dumps(run(args.original, args.output, args.manifest), indent=2))

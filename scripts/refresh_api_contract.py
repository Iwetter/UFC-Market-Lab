"""Verify the implemented wire contract against the official current specs.

Never silently adapts execution payloads to a changed upstream specification.
"""
import hashlib
import json
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

import yaml

root=Path(__file__).resolve().parents[1]
report={"verified_utc":datetime.now(timezone.utc).isoformat(),"sources":{}}
specs={}
for filename in ('openapi.yaml','asyncapi.yaml'):
    url='https://docs.kalshi.com/'+filename
    raw=urllib.request.urlopen(url,timeout=30).read()
    report['sources'][url]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    specs[filename]=yaml.safe_load(raw)
schemas=specs['openapi.yaml']['components']['schemas']
required=set(schemas['CreateOrderV2Request']['required'])
if required != {'ticker','side','count','price','time_in_force','self_trade_prevention_type'}:
    raise SystemExit('CreateOrderV2 required fields changed. Stop and review the integration.')
if schemas['BookSide']['enum'] != ['bid','ask']:raise SystemExit('Order side contract changed')
if '/portfolio/events/orders' not in specs['openapi.yaml']['paths']:raise SystemExit('V2 order path missing')
report['checks']={'v2_required_fields':sorted(required),'side_enum':['bid','ask'],'order_path':'/portfolio/events/orders','passed':True}
path=root/'docs/api-contract-verification.json'
path.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

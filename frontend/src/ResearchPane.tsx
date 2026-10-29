import {useEffect,useState} from 'react';
import {Activity,ArrowRight,Database,FlaskConical,RefreshCw,ShieldCheck} from 'lucide-react';
import {pct} from './format';

type Obj=Record<string,any>;
const number=(v:unknown)=>v==null?'—':Number(v).toLocaleString('en-US');
const score=(v:unknown)=>v==null?'—':Number(v).toFixed(4);
const seconds=(v:unknown)=>v==null?'unmeasured':`${(Number(v)/1000).toFixed(2)} s`;
const interval=(v:unknown)=>Array.isArray(v)?`${seconds(v[0])} to ${seconds(v[1])}`:'Not measured';

export function ResearchPane({market,token,onNotice}:{market?:Obj,token:string,onNotice:(s:string)=>void}){
 const [reports,setReports]=useState<Obj|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function load(){setBusy(true);setError('');try{
  const values=await Promise.all(['data','latency','positions'].map(async key=>{
   const response=await fetch(`/api/research/${key}`);
   const body=await response.json();if(!response.ok)throw Error(body.detail||`Could not load ${key}`);return body;
  }));setReports({data:values[0],latency:values[1],positions:values[2]});
 }catch(e){setError(String(e));}finally{setBusy(false);}}
 useEffect(()=>{void load();},[]);
 async function tag(event_kind:string){if(!market?.mapping)return;setBusy(true);try{
  const response=await fetch('/api/research/latency-annotations',{method:'POST',headers:{'Content-Type':'application/json','X-Research-Token':token},body:JSON.stringify({
   id:crypto.randomUUID(),bout_id:market.mapping.bout_id,card_id:market.mapping.card_id,ticker:market.ticker,
   event_kind,source:'manual_broadcast',broadcast_observed_ms:Date.now(),direction:'either'
  })});const body=await response.json();if(!response.ok)throw Error(body.detail||'Observation rejected');
  onNotice(`${event_kind.replaceAll('_',' ')} observation recorded${body.is_mock?' as MOCK; excluded from real timing evidence':''}. This is broadcast-observation time.`);await load();
 }catch(e){onNotice(String(e));}finally{setBusy(false);}}
 const history=reports?.data?.historical,pre=history?.prefight_evaluation,round=history?.round_scoring_evaluation;
 const timing=reports?.latency,positions=reports?.positions;
 return <div className="research-page">
  <div className="research-toolbar"><span><FlaskConical size={15}/> Research evidence never authorizes an order.</span><button className="subtle" disabled={busy} onClick={()=>void load()}><RefreshCw size={13}/> Refresh findings</button></div>
  {error&&<p role="alert" className="research-error">{error}</p>}
  <div className="research-counts">
   <ResearchMetric label="REAL HISTORICAL BOUTS" value={number(history?.prefight_samples?.source_fights)} detail="Pinned CC BY 4.0 source"/>
   <ResearchMetric label="LAGGED PRE-FIGHT SAMPLES" value={number(history?.prefight_samples?.samples)} detail="Earlier bouts only; 7-day embargo"/>
   <ResearchMetric label="PHYSICAL TIMING OBSERVATIONS" value={number(timing?.summaries?.physical_delay_ms?.n)} detail="Synthetic captures excluded"/>
   <ResearchMetric label="EXACT POSITION EVIDENCE" value={number(positions?.independent_bouts)} detail="Independent bouts, not repeated frames"/>
  </div>
  <div className="research-grid"><section className="panel"><div className="panel-heading"><h2>Training on real history</h2><Database size={16}/></div><div className="research-body">
   <p>A separate baseline uses earlier fight results, striking context, grappling rates and Elo. Historical snapshot reconstruction cannot establish what was available at the time of a live trade.</p>
   <div className="research-partitions"><span><b>TRAIN</b> Through 2022</span><span><b>CALIBRATE</b> 2023</span><span><b>TEST</b> 2024–2026</span></div>
   <div className="table-scroll"><table><thead><tr><th>PRE-FIGHT HOLDOUT</th><th>LOG LOSS ↓</th><th>BRIER ↓</th></tr></thead><tbody>
    <tr><td>Historical model · {number(pre?.test_metrics?.n)} bouts</td><td>{score(pre?.test_metrics?.log_loss)}</td><td>{score(pre?.test_metrics?.brier)}</td></tr>
    <tr><td>Earlier-result Elo baseline</td><td>{score(pre?.test_elo_metrics?.log_loss)}</td><td>{score(pre?.test_elo_metrics?.brier)}</td></tr>
    <tr><td>50/50 baseline</td><td>{score(pre?.test_coinflip_metrics?.log_loss)}</td><td>{score(pre?.test_coinflip_metrics?.brier)}</td></tr>
   </tbody></table></div>
   <p className="research-note-text">Accuracy: {pct(pre?.test_accuracy)}. No contemporaneous Kalshi benchmark or execution costs are available for this comparison.</p>
   <div className="model-fact"><span>Round judging model</span><b>{number(round?.test_metrics?.n)} completed decision rounds</b></div>
   <div className="model-fact"><span>Round score accuracy</span><b>{pct(round?.test_accuracy)}</b></div>
   <p className="research-note-text">Round judging predicts a majority round result. It is not fight-win probability. Decision-only selection is recorded in the report.</p>
   <div className="research-safety"><ShieldCheck size={15}/><span>Historical artifacts cannot be loaded as trading-approved models.</span></div>
   {history&&<a href={history.source_repository} target="_blank" rel="noreferrer">Source dataset <ArrowRight size={13}/><code>{history.source_commit?.slice(0,12)}</code></a>}
  </div></section>
  <section className="panel"><div className="panel-heading"><h2>Is the market 12 seconds late?</h2><Activity size={16}/></div><div className="research-body">
   <div className="hypothesis-state"><span>PHYSICAL EVENT → BOOK REACTION</span><strong>{timing?.hypothesis?.status?.replaceAll('_',' ')||'Loading observations'}</strong><small>12 ± 2 seconds is the registered hypothesis, not a measured constant.</small></div>
   <div className="model-fact"><span>Physical delay interval</span><b>{interval(timing?.summaries?.physical_delay_ms?.median_interval_ms)}</b></div>
   <div className="model-fact"><span>Lead after data reaches us</span><b>{interval(timing?.summaries?.available_lead_ms?.median_interval_ms)}</b></div>
   <div className="model-fact"><span>Relative to broadcast</span><b>{interval(timing?.summaries?.broadcast_relative_delay_ms?.median_interval_ms)}</b></div>
   <div className="model-fact"><span>Broadcast 12-second hypothesis</span><b>{timing?.broadcast_hypothesis?.status?.replaceAll('_',' ')||'Not measured'}</b></div>
   <div className="model-fact"><span>Independent cards required</span><b>{number(timing?.hypothesis?.required_independent_cards)}</b></div>
   <p>We compare executable bids and asks. A stale last-traded price does not prove that an order could still execute at that price. Packet generation, broadcast viewing and physical occurrence use separate clocks.</p>
   <div className="timing-tag-box"><b>Tag what you see now</b><span>{market?.ticker||'Select a market in Workspace first'}</span><div>{['knockdown','round_start','finish'].map(kind=><button key={kind} disabled={busy||!market?.mapping} onClick={()=>void tag(kind)}>{kind.replaceAll('_',' ')}</button>)}</div><small>Broadcast observations only. Mock tags remain synthetic. Confirm mappings and capture sufficient pre/post book depth before interpreting a reaction.</small></div>
   {timing?.capture_warnings?.map((w:string)=><p className="research-note-text" key={w}>{w}</p>)}<div className="stress-chips">{timing?.stress_scenarios?.map((s:Obj)=><span key={s.name}>+{s.additional_feed_delay_ms/1000}s stress</span>)}</div><p className="research-note-text">Replay stresses are assumptions, not observed delays.</p>
  </div></section></div>
  <section className="panel research-positions"><div className="panel-heading"><h2>Grappling-position probabilities need a denominator</h2><span className="tiny-label">{positions?.status?.replaceAll('_',' ').toUpperCase()||'LOADING'}</span></div><div className="research-body"><p>The acquired public history records distance, clinch and ground strike contexts. It does not tell us how many fighters were in mount or back control at a chosen moment. Finish-only tables cannot supply that denominator.</p><div className="position-grid">{[{label:'Mount',key:'mount'},{label:'Back control',key:'back_control'},{label:'Guard',key:'closed_guard'},{label:'Side control',key:'side_control'}].map(item=><PositionTile key={item.key} label={item.label} estimate={positions?.estimates?.find((e:Obj)=>e.position===item.key)}/>)}</div><p className="research-note-text">The position-study API accepts reviewed observations at a predeclared fight-clock checkpoint, counts each bout once and reports uncertainty. No positional percentage is invented.</p></div></section>
 </div>;
}
function ResearchMetric({label,value,detail}:{label:string,value:string,detail:string}){return <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;}

function PositionTile({label,estimate}:{label:string,estimate?:Obj}){return <div><span>{label}</span><strong>{estimate?.win_probability==null?'Unmeasured':pct(estimate.win_probability)}</strong><small>{estimate?`${estimate.independent_bouts} independent bouts · descriptive only`:'Licensed exposure observations required'}</small></div>;}

export const money=(n:unknown)=>Number(n??0).toLocaleString('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:2});
export const pct=(n:unknown)=>n==null?'—':`${(Number(n)*100).toFixed(1)}%`;
export const cents=(n:unknown)=>n==null?'—':`${(Number(n)*100).toFixed(1)}¢`;
export const age=(n:unknown)=>n==null?'unavailable':Number(n)<1000?`${Math.round(Number(n))} ms`:`${(Number(n)/1000).toFixed(1)} s`;
export const priceForLimit=(price:unknown,slippage:number,bands:{start:string,end:string,step:string}[],outcome:string,buy=true)=>{
 const target=Number(price)+(buy?slippage:-slippage);
 const values=bands.flatMap(b=>{const step=Number(b.step),start=Number(b.start),end=Number(b.end),yes=outcome==='yes'?target:1-target;
  const units=(yes-start)/step;const round=(buy===(outcome==='yes'))?Math.ceil(units-1e-8):Math.floor(units+1e-8);
  const p=start+round*step;return p>=start&&p<=end&&p>0&&p<1?[outcome==='yes'?p:1-p]:[];});
 return values.length?(buy?Math.min(...values):Math.max(...values)).toFixed(6):'';
};

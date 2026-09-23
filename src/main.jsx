import React,{useEffect,useMemo,useState}from'react';
import{createRoot}from'react-dom/client';
import{Search,RefreshCw,Zap,TrendingUp,TrendingDown,Users,Trophy,ArrowUpDown}from'lucide-react';
import'./style.css';
const POS=['ALL','QB','RB','WR','TE','K','FLX','DST'];
const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};
const fmt=(v,d=1)=>num(v)===null?'N/A':num(v).toFixed(d);
const pct=v=>num(v)===null?'N/A':`${Math.round(num(v))}%`;
const norm=v=>String(v||'').toLowerCase().replace(/[^a-z0-9 ]/g,'').replace(/\s+/g,' ').trim();
function App(){
 const[data,setData]=useState(null),[q,setQ]=useState(''),[pos,setPos]=useState('ALL'),[sort,setSort]=useState('projection'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[leagueId,setLeagueId]=useState(''),[view,setView]=useState('players');
 async function load(){setBusy(true);setError('');try{const r=await fetch(`./data/latest.json?v=${Date.now()}`,{cache:'no-store'});if(!r.ok)throw new Error(`Data request failed: ${r.status}`);const p=await r.json();setData(p);setLeagueId(x=>x||p.espnLeagues?.find(l=>l.connected)?.id||'')}catch(e){setError(e.message)}finally{setBusy(false)}}
 useEffect(()=>{load()},[]);
 const league=data?.espnLeagues?.find(l=>String(l.id)===String(leagueId));
 const myTeam=league?.teams?.find(t=>String(t.id)===String(league.myTeamId));
 const opponentId=league?.matchup?(String(league.matchup.homeTeamId)===String(league.myTeamId)?league.matchup.awayTeamId:league.matchup.homeTeamId):null;
 const opponent=league?.teams?.find(t=>String(t.id)===String(opponentId));
 const rosterNames=new Set((league?.teams||[]).flatMap(t=>t.roster||[]).map(p=>norm(p.name)));
 const playerMap=useMemo(()=>new Map((data?.players||[]).map(p=>[norm(p.name),p])),[data]);
 const value=p=>({...p,pv:num(p.projection),bv:num(p.boom),uv:num(p.bust),tv:num(p.trend)});
 const all=useMemo(()=>{if(!data)return[];return data.players.map(value).filter(p=>(pos==='ALL'||(pos==='FLX'?(p.flexEligible||['RB','WR','TE'].includes(p.pos)):p.pos===pos))&&`${p.name} ${p.team} ${p.pos}`.toLowerCase().includes(q.toLowerCase())).sort((a,b)=>sort==='boom'?(b.bv??-1)-(a.bv??-1):sort==='bust'?(a.uv??101)-(b.uv??101):sort==='name'?a.name.localeCompare(b.name):(b.pv??-1)-(a.pv??-1))},[data,q,pos,sort]);
 const rosterView=(team,startersOnly=false)=>(team?.roster||[]).filter(r=>!startersOnly||r.starter).map(r=>value(playerMap.get(norm(r.name))||{name:r.name,pos:r.lineupSlot,team:'',projection:r.projection,boom:r.boom,bust:r.bust,confidence:r.confidence,injury:r.injuryStatus}));
 const mine=rosterView(myTeam),opp=rosterView(opponent),free=all.filter(p=>!rosterNames.has(norm(p.name))).slice(0,80);
 const projectedTotal=team=>rosterView(team,true).reduce((s,p)=>s+(p.pv||0),0);
 const displayed=view==='team'?mine:view==='waivers'?free:all;
 if(!data&&!error)return <div className="loading">Loading fantasy data...</div>;
 return <><header><div className="brand"><span><Zap/></span><div><h1>BOOM/BUST LAB</h1><small>Custom nflverse model · ESPN multi-league</small></div></div><button className="primary" onClick={load}><RefreshCw className={busy?'spin':''}/>{busy?'Refreshing':'Refresh'}</button></header><main>{error&&<div className="notice">{error}</div>}
 {data&&<><section className="tools"><label><Search/><input value={q} onChange={e=>setQ(e.target.value)} placeholder="Search players"/></label><div>{data.espnLeagues?.length>0&&<select value={leagueId} onChange={e=>setLeagueId(e.target.value)}>{data.espnLeagues.map(l=><option key={l.id} value={l.id}>{l.name}{l.connected?'':' (not connected)'}</option>)}</select>}<button className={view==='players'?'active':''} onClick={()=>setView('players')}>All Players</button><button className={view==='team'?'active':''} onClick={()=>setView('team')}>My Team</button><button className={view==='waivers'?'active':''} onClick={()=>setView('waivers')}>Waivers</button><button className={view==='matchup'?'active':''} onClick={()=>setView('matchup')}>Matchup</button></div></section>
 <section className="cards"><Card label="League" value={league?.name||'Not connected'}/><Card label="My Team" value={myTeam?.name||'Not identified'}/><Card label="Week" value={data.week}/><Card label="Scoring" value={data.scoring}/></section>
 {view==='matchup'?<Matchup mine={myTeam} opponent={opponent} mineTotal={projectedTotal(myTeam)} oppTotal={projectedTotal(opponent)} rosterView={rosterView}/>:<><section className="tools"><div>{POS.map(x=><button key={x} className={pos===x?'active':''} onClick={()=>setPos(x)}>{x}</button>)}<select value={sort} onChange={e=>setSort(e.target.value)}><option value="projection">Projection</option><option value="boom">Boom %</option><option value="bust">Lowest bust</option><option value="name">Name</option></select></div></section><PlayerTable players={displayed}/></>}
 </>}</main></>}
function PlayerTable({players}){return <section className="table"><table><thead><tr><th>Player</th><th>Matchup</th><th>Projection</th><th>Floor / Ceiling</th><th>Boom</th><th>Bust</th><th>Trend</th><th>Confidence</th><th>Injury</th></tr></thead><tbody>{players.map((p,i)=><tr key={`${p.id||p.name}-${i}`}><td><strong>{p.name}</strong><small>{p.pos} · {p.team}{p.flexEligible?' · FLX eligible':''}</small></td><td>{p.opp||'N/A'}</td><td className="points">{fmt(p.pv)}</td><td>{fmt(p.floor)} / {fmt(p.ceiling)}</td><td className="boom">{pct(p.bv)}</td><td className="bust">{pct(p.uv)}</td><td className={p.tv!==null&&p.tv>=0?'boom':'bust'}>{p.tv===null?'N/A':<>{p.tv>=0?<TrendingUp/>:<TrendingDown/>}{Math.abs(p.tv).toFixed(2)}</>}</td><td>{pct(p.confidence)}</td><td>{p.injury||'Healthy'}</td></tr>)}</tbody></table></section>}
function Matchup({mine,opponent,mineTotal,oppTotal,rosterView}){return <><section className="cards"><Card label={mine?.name||'My Team'} value={fmt(mineTotal)}/><Card label={opponent?.name||'Opponent'} value={fmt(oppTotal)}/><Card label="Projected Edge" value={`${mineTotal>=oppTotal?'+':''}${(mineTotal-oppTotal).toFixed(1)}`}/><Card label="Leader" value={mineTotal>=oppTotal?(mine?.name||'My Team'):(opponent?.name||'Opponent')}/></section><h2><Users/> My starters</h2><PlayerTable players={rosterView(mine,true)}/><h2><Trophy/> Opponent starters</h2><PlayerTable players={rosterView(opponent,true)}/></>}
function Card({label,value}){return <div><small>{label}</small><b>{value}</b></div>}
createRoot(document.getElementById('root')).render(<App/>);

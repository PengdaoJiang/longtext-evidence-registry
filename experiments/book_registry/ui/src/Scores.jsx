import React,{useState} from 'react';
import {useData} from './api';
import {kinds,date} from './labels';
import Reference from './Reference';

const actions={keep:'独立保留',merge:'合并保存',source_only:'仅留原文',review:'待复核'};
const number=p=>p==null?'—':Number(p).toFixed(2);
const sourceCode=pid=>{const m=/SU-(\d+)-P(\d+)/.exec(pid);return m?`P${Number(m[1])}-${Number(m[2])}`:pid;};

export function ScoreDetail({score,onEntity,onSource}) {
  if(!score)return null;
  return <section className="score-detail">
    <div className="score-line"><span>独立保留分</span><b>{number(score.p_need)}</b><span>{actions[score.recommendation]}</span></div>
    <small>{score.model} · {score.reasoning_effort} · 未校准 · 门限关闭</small>
    <p>{score.reason}</p>
    {score.merge_target_id?<button className="score-target" onClick={()=>onEntity(score.merge_target_id)}>并入 {score.merge_target_label} · {score.merge_target_handle}</button>:null}
    {score.preserve_note?<p className="score-note">合并须保留：{score.preserve_note}</p>:null}
    {score.context_gap?<p className="score-gap">语境缺口：{score.context_gap}</p>:null}
    <div className="score-sources">{score.evidence_paragraph_ids.map(pid=><button key={pid} onClick={()=>onSource(pid)}>{sourceCode(pid)} ↗</button>)}</div>
    <details><summary>评分记录</summary><small>{score.run_id}<br/>{score.method}<br/>{date(score.created_at)}</small></details>
  </section>;
}

export default function Scores({version,onEntity,selected}) {
  const [order,setOrder]=useState('asc'),[offset,setOffset]=useState(0);
  const resource=useData('scores',{order,offset},version),data=resource.data;
  return <div className="fact-panel score-panel">
    <div className="table-info"><span>试评分 {data?.total??'…'} / {data?.run?.expected??'…'} · Luna Max · 未校准 · 门限关闭</span><select aria-label="评分排序" value={order} onChange={e=>{setOrder(e.target.value);setOffset(0);}}><option value="asc">分数从低到高</option><option value="desc">分数从高到低</option></select></div>
    {resource.error?<div className="error">{resource.error}</div>:null}
    <div className="table-scroll"><table><thead><tr><th>编号</th><th>条目</th><th>保留分</th><th>Luna 建议</th></tr></thead><tbody>{data?.items.map(s=><tr key={s.score_id} className={selected===s.entity_id?'selected':''} tabIndex="0" onClick={()=>onEntity(s.entity_id)} onKeyDown={e=>e.key==='Enter'&&onEntity(s.entity_id)}><td><Reference code={s.handle}/></td><td>{s.label}<small>{kinds[s.kind]||s.kind}</small></td><td className="score-value">{number(s.p_need)}</td><td>{actions[s.recommendation]}</td></tr>)}</tbody></table>{data?.total===0?<div className="empty">{data.run?.status==='running'?'Luna 正在评分':'暂无评分'}</div>:null}</div>
    <div className="pagination"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-60))}>‹ 上页</button><span>{data?.total?`${offset+1}–${Math.min(offset+60,data.total)} / ${data.total}`:'0'}</span><button disabled={offset+60>=(data?.total||0)} onClick={()=>setOffset(offset+60)}>下页 ›</button></div>
  </div>;
}

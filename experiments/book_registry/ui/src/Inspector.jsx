import React from 'react';
import {states,kinds,pred,modality,attribution,date,colors,valueText} from './labels';
import Reference from './Reference';
import {ScoreDetail} from './Scores';

export function Evidence({items=[],onSource,title='原文'}) {
  return items.length?<section className="evidence"><h3>{title}<span>{items.length}</span></h3>{items.map((e,i)=>{
    const chars=Array.from(e.paragraph_text||e.quote);
    return <div className="source" key={e.evidence_id||i}>
      <div className="source-heading"><Reference code={e.paragraph_handle}/><button className="source-link" title={e.paragraph_id} onClick={()=>onSource(e.paragraph_id)}>↗</button></div>
      <p>{e.paragraph_text?<>{chars.slice(0,e.start).join('')}<mark>{chars.slice(e.start,e.end).join('')}</mark>{chars.slice(e.end).join('')}</>:e.quote}</p>
    </div>;
  })}</section>:null;
}

export default function Inspector({selection,resource,onSource,onEntity,onFact}) {
  const d=resource.data;
  if(resource.error)return <aside className="inspector"><div className="error">{resource.error}</div></aside>;
  if(!d)return <aside className="inspector"/>;
  const isFact=selection.type==='fact',current=isFact?d.review_state:d.record_review?.state||'pending';
  const related=selection.related||[],page=related.indexOf(selection.id);
  return <aside className="inspector">
    <header className="inspector-head"><h2>{isFact?'事实':'条目'}</h2>
      {related.length>1?<div className="relation-pages"><button aria-label="上一条连接事实" disabled={page<=0} onClick={()=>onFact(related[page-1],related)}>‹</button><span>{page+1}/{related.length}</span><button aria-label="下一条连接事实" disabled={page>=related.length-1} onClick={()=>onFact(related[page+1],related)}>›</button></div>:null}
      <span className={'state '+current}>{states[current]}</span>
    </header>
    <div className="inspector-scroll">
      <div className="feedback-reference"><Reference code={d.handle}/></div>
      {isFact?<>
        <div className="assertion"><button onClick={()=>onEntity(d.subject_id)}>{d.subject_label}<small className="record-code">{d.subject_handle}</small></button><div className="assertion-predicate">{pred(d.predicate)}<span>⟶</span></div>{d.object_id?<button onClick={()=>onEntity(d.object_id)}>{d.object_label}<small className="record-code">{d.object_handle}</small></button>:<p>{valueText(d.value)}</p>}</div>
        <div className="qualifier-row"><span>{modality[d.modality]||d.modality}</span><span>{attribution[d.attribution]||d.attribution}</span>{d.polarity==='negative'?<strong>否定</strong>:null}</div>
        <Evidence items={d.evidence} onSource={onSource}/>
        <dl className="metadata"><dt>说话者</dt><dd>{d.speaker_label||'—'}</dd><dt>框架</dt><dd>{d.frame}</dd><dt>故事时间</dt><dd>{d.story_time||'—'}</dd></dl>
        <Evidence items={d.context_support} onSource={onSource} title="关联原文"/>
        <details><summary>登记记录 · {d.provenance?.length||0}</summary>{d.provenance?.map(p=><div className="history" key={p.submission_id}><b>{p.model}</b><p>{p.batch_id}</p><small>{date(p.created_at)}</small></div>)}</details>
      </>:<>
        <div className="entity-heading"><span className="kind-symbol" style={{color:colors[d.kind]||'#8a96a0'}}>●</span><h2>{d.label}</h2><small>{kinds[d.kind]||d.kind} · {d.status}</small></div>
        <ScoreDetail score={d.admission_score} onEntity={onEntity} onSource={onSource}/>
        <dl className="metadata"><dt>称形</dt><dd>{[...new Set(d.names?.map(n=>n.surface))].join(' / ')||'—'}</dd><dt>提及</dt><dd>{d.mention_count}</dd></dl>
        {d.identity_links?.length?<section><h3>身份记录</h3>{d.identity_links.map(f=><button className="identity-row" key={f.fact_id} onClick={()=>onFact(f.fact_id)}><span className={'dot '+f.review_state}/><span><code>{f.handle}</code> · {f.subject_label} → {f.predicate} → {f.object_label}</span></button>)}</section>:null}
        <Evidence items={d.evidence} onSource={onSource}/><Evidence items={d.context_support} onSource={onSource} title="关联原文"/>
        <details><summary>提及 · {d.mentions?.length||0}/{d.mention_count}</summary>{d.mentions?.map(m=><div className="mention-row" key={m.mention_id}><Reference code={m.handle}/><button onClick={()=>onSource(m.paragraph_id)}>{m.paragraph_handle}</button><q>{m.quote}</q></div>)}</details>
      </>}
      <details className="reviews"><summary>复核记录 · {d.reviews?.length||0}</summary>{d.reviews?.map(r=><div className="history" key={r.revision}><b className={'state '+r.state}>{states[r.state]}</b><small>{r.reviewer} · {date(r.created_at)}</small><p>{r.reason}</p></div>)}</details>
    </div>
  </aside>;
}

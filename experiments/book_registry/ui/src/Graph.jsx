import React, {useMemo,useRef,useState} from 'react';
import {colors,kinds,pred,shortName} from './labels';

function layout(data) {
  if (!data) return {nodes:[],links:[]};
  const neighbours=data.nodes.filter(n=>n.entity_id!==data.focus);
  const nodes=data.nodes.map(n=>{
    const i=neighbours.indexOf(n),angle=-Math.PI/2+i*2*Math.PI/Math.max(1,neighbours.length);
    return {...n,id:n.entity_id,x:n.entity_id===data.focus?0:Math.cos(angle)*330,y:n.entity_id===data.focus?0:Math.sin(angle)*210};
  });
  const byId=new Map(nodes.map(n=>[n.id,n])),pairs=new Map();
  for (const fact of data.edges) {
    const key=fact.subject_id+'|'+fact.object_id;
    const edge=pairs.get(key)||{source:byId.get(fact.subject_id),target:byId.get(fact.object_id),facts:[]};
    edge.facts.push(fact);pairs.set(key,edge);
  }
  const occupied=nodes.map(n=>({x:n.x,y:n.y+20,w:65,h:63}));
  const links=[...pairs.values()].map((edge,index)=>{
    const a=edge.source,b=edge.target,dx=b.x-a.x,dy=b.y-a.y,dist=Math.hypot(dx,dy)||1;
    const sx=a.x+dx/dist*29,sy=a.y+dy/dist*29,tx=b.x-dx/dist*34,ty=b.y-dy/dist*34;
    const isNeighbourLink=a.id!==data.focus&&b.id!==data.focus;
    const bend=isNeighbourLink?190:16*(index%2?1:-1);
    let cx=(sx+tx)/2-dy/dist*bend,cy=(sy+ty)/2+dx/dist*bend;
    if(isNeighbourLink&&Math.hypot(cx,cy)<100){cx=(sx+tx)/2+dy/dist*bend;cy=(sy+ty)/2-dx/dist*bend;}
    const base={x:(sx+2*cx+tx)/4,y:(sy+2*cy+ty)/4-9};
    let best=null;
    // Keep label hit areas above all lines and outside neighbouring nodes.
    for(let ox=-3;ox<=3;ox++)for(let oy=-3;oy<=3;oy++){
      const x=Math.max(-360,Math.min(360,base.x+ox*34)),y=Math.max(-230,Math.min(230,base.y+oy*27));
      const collisions=occupied.reduce((score,p)=>score+(Math.abs(x-p.x)<p.w+47&&Math.abs(y-p.y)<p.h+17?1:0),0);
      const score=collisions*100000+(x-base.x)**2+(y-base.y)**2;
      if(!best||score<best.score)best={x,y,score};
    }
    occupied.push({...best,w:47,h:17});
    return {...edge,d:`M${sx},${sy} Q${cx},${cy} ${tx},${ty}`,label:best};
  });
  return {nodes,links};
}

export default function Graph({data,onEntity,onFact,selected}) {
  const [zoom,setZoom]=useState(1),[pan,setPan]=useState({x:0,y:0});
  const drag=useRef(null),model=useMemo(()=>layout(data),[data]);
  if(!data)return <div className="empty">正在读取关系…</div>;
  const factFor=edge=>edge.facts.find(f=>f.fact_id===selected)||edge.facts[0];
  const choose=edge=>onFact(factFor(edge).fact_id,edge.facts.map(f=>f.fact_id));
  const styleFor=edge=>{const f=factFor(edge);return `graph-edge ${f.review_state} ${edge.facts.some(f=>f.fact_id===selected)?'chosen':''}`;};
  return <div className="graph-surface">
    <div className="canvas-caption"><span>{model.nodes.length} 个对象 · {data.edges.length}{data.total_edges>data.edges.length?` / ${data.total_edges}`:''} 条联系</span></div>
    <div className="zoom"><button aria-label="缩小关系图" onClick={()=>setZoom(z=>Math.max(.55,z-.15))}>−</button><button onClick={()=>{setZoom(1);setPan({x:0,y:0});}}>{Math.round(zoom*100)}%</button><button aria-label="放大关系图" onClick={()=>setZoom(z=>Math.min(1.75,z+.15))}>＋</button></div>
    <svg className="network" viewBox="-450 -310 900 640" aria-label="实体关系图"
      onPointerDown={e=>{if(e.target===e.currentTarget){drag.current={x:e.clientX,y:e.clientY,pan};e.currentTarget.setPointerCapture(e.pointerId);}}}
      onPointerMove={e=>{if(drag.current){const ratio=900/e.currentTarget.getBoundingClientRect().width;setPan({x:drag.current.pan.x+(e.clientX-drag.current.x)*ratio,y:drag.current.pan.y+(e.clientY-drag.current.y)*ratio});}}}
      onPointerUp={()=>drag.current=null} onPointerCancel={()=>drag.current=null}>
      <defs>{['accepted','pending','rejected'].map(s=><marker key={s} id={'arrow-'+s} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill={s==='accepted'?'#258777':s==='pending'?'#ce972a':'#bd6e75'}/></marker>)}</defs>
      <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
        {model.links.map(edge=><g className={styleFor(edge)} key={edge.source.id+edge.target.id} onClick={()=>choose(edge)}>
          <path className="edge-hit" d={edge.d}/><path className="edge-line" d={edge.d} markerEnd={`url(#arrow-${factFor(edge).review_state})`}/>
        </g>)}
        {model.links.map(edge=>{const f=factFor(edge),a=edge.source,b=edge.target,label=pred(f.predicate)+(edge.facts.length>1?` · ${edge.facts.length}`:'');return <g className={styleFor(edge)} key={a.id+b.id} role="button" tabIndex="0" aria-label={`${a.label} ${pred(f.predicate)} ${b.label}`} onClick={()=>choose(edge)} onKeyDown={e=>e.key==='Enter'&&choose(edge)} transform={`translate(${edge.label.x},${edge.label.y})`}>
          <rect className="edge-label-hit" x={-Math.max(47,label.length*7)} y="-14" width={Math.max(94,label.length*14)} height="34" rx="4"/>
          <text textAnchor="middle">{label}<tspan className="edge-code" x="0" dy="16">{f.handle}</tspan></text>
          <title>{edge.facts.map(f=>`${f.handle} · ${pred(f.predicate)} · ${f.note||''}`).join('\n')}</title>
        </g>;})}
        {model.nodes.map(n=>{const center=n.id===data.focus,name=shortName(n.label),color=colors[n.kind]||'#82929b',diamond=['item','location','place','scene','faction','concept'].includes(n.kind);return <g key={n.id} className={'graph-node '+(center?'focus':'')} transform={`translate(${n.x},${n.y})`} role="button" tabIndex="0" aria-label={`查看条目 ${n.label}`} onClick={e=>{e.stopPropagation();setPan({x:0,y:0});onEntity(n.id);}} onKeyDown={e=>e.key==='Enter'&&onEntity(n.id)}>
          {center?<circle r="42" fill="none" stroke={color} strokeOpacity=".14" strokeWidth="9"/>:null}
          {diamond?<rect x="-23" y="-23" width="46" height="46" rx="4" transform="rotate(45)" fill={color+'13'} stroke={color} strokeWidth="1.6"/>:<circle r={center?33:27} fill={color+'13'} stroke={color} strokeWidth="1.6"/>}
          {n.kind==='person'?<g fill={color}><circle cy="-7" r="6"/><path d="M-11 13v-4a11 10 0 0 1 22 0v4z"/></g>:<text className="node-glyph" y="5" fill={color} textAnchor="middle">{(kinds[n.kind]||n.kind)[0]}</text>}
          <text className="node-label" y={center?57:50} textAnchor="middle">{name.length>13?name.slice(0,12)+'…':name}</text><text className="node-code" y={center?74:67} textAnchor="middle">{n.handle}</text><title>{n.handle} · {n.label} · {kinds[n.kind]||n.kind}</title>
        </g>;})}
      </g>
    </svg>
    {!data.edges.length?<div className="graph-empty">0 条联系</div>:null}
    <div className="legend"><span><i className="line accepted"/>已接受</span><span><i className="line pending"/>待核</span><span><i className="line rejected"/>已否决</span></div>
  </div>;
}

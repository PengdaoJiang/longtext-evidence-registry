import React,{useDeferredValue,useEffect,useState} from 'react';
import {get,useData} from './api';
import {states} from './labels';
import Graph from './Graph';
import Inspector from './Inspector';
import Scores from './Scores';
import {EntityRail,Facts,Chapter,Tracks} from './Panels';

const initial=new URLSearchParams(location.hash.slice(1));
export default function App(){
 const [tab,setTab]=useState(initial.get('view')||'facts'),[focus,setFocus]=useState(initial.get('entity')||'');
 const [selection,setSelection]=useState({type:initial.get('fact')?'fact':'entity',id:initial.get('fact')||focus});
 const [query,setQuery]=useState(''),[kind,setKind]=useState(''),[entityOffset,setEntityOffset]=useState(0),[searchNotice,setSearchNotice]=useState('');
 const [status,setStatus]=useState(null),[connection,setConnection]=useState('正在连接'),[manual,setManual]=useState(0);
 const [state,setState]=useState(initial.get('state')||'active'),[events,setEvents]=useState(initial.get('events')==='1'),[scope,setScope]=useState(initial.get('scope')||'entity'),[unit,setUnit]=useState(initial.get('unit')||'SU-000001'),[paragraph,setParagraph]=useState(initial.get('paragraph')||''),[highlight,setHighlight]=useState(initial.get('highlight')||''),[factOffset,setFactOffset]=useState(0);
 const deferredQuery=useDeferredValue(query);const version=(status?.generation||'')+':'+manual;
 useEffect(()=>{let alive=true,requesting=false;async function poll(){if(requesting)return;requesting=true;try{const next=await get('status');if(alive){setStatus(next);setConnection('书库已连接');}}catch(e){if(alive)setConnection('连接中断');}finally{requesting=false;}}poll();const timer=setInterval(poll,5000);return()=>{alive=false;clearInterval(timer);};},[]);
 useEffect(()=>{setEntityOffset(0);},[deferredQuery,kind]);
 useEffect(()=>{setFactOffset(0);},[state,scope,unit,paragraph,focus]);
 useEffect(()=>{const p=new URLSearchParams({view:tab,entity:focus,unit,state,scope});if(selection.type==='fact')p.set('fact',selection.id);if(paragraph)p.set('paragraph',paragraph);if(highlight)p.set('highlight',highlight);if(events)p.set('events','1');history.replaceState(null,'','#'+p);},[tab,focus,selection,unit,state,scope,paragraph,highlight,events]);
 const entities=useData('entities',{q:deferredQuery,kind,offset:entityOffset},version);
 const firstEntity=entities.data?.items?.[0]?.entity_id;
 useEffect(()=>{if(!focus&&firstEntity){setFocus(firstEntity);setSelection({type:'entity',id:firstEntity});}},[focus,firstEntity]);
 const graph=useData('graph',{id:focus,state,events:events?'1':'0'},version,Boolean(focus));
 const factList=useData('facts',{state,entity:scope==='entity'?focus:'',unit:scope==='chapter'?unit:'',paragraph:scope==='paragraph'?paragraph:'',offset:factOffset},version);
 const detail=useData(selection.type,{id:selection.id},version,Boolean(selection.id));
 function onEntity(id){setFocus(id);setSelection({type:'entity',id});setScope('entity');}
 function onFact(id,related=[]){setSelection({type:'fact',id,related});}
 function onSource(pid){setUnit(pid.split('-P')[0]);setHighlight(pid);setTab('chapter');}
 function onChapter(uid){setUnit(uid);setHighlight('');setTab('chapter');}
 function onParagraph(pid){setParagraph(pid);setScope('paragraph');setTab('facts');setState('all');}
 async function jumpReference(){if(!/^(?:[EFM]\d+|P\d+-\d+)$/i.test(query.trim()))return;try{const target=await get('resolve',{code:query});setSearchNotice('');setQuery('');setKind('');if(target.record_type==='entity'){onEntity(target.record_id);setTab('graph');}else if(target.record_type==='fact'){setFocus(target.entity_id);setSelection({type:'fact',id:target.record_id});setScope('entity');setTab('facts');setState('all');}else {if(target.entity_id)onEntity(target.entity_id);onSource(target.paragraph_id);}}catch(e){setSearchNotice(e.message);}}
 const refresh=()=>setManual(x=>x+1);
 return <div className="app"><header className="topbar"><h1>长文关系 <span>· 审阅台</span></h1><nav>{[['graph','关系图'],['facts','事实清单'],['chapter','章节原文'],['scores','试评分']].map(([key,label])=><button key={key} className={tab===key?'active':''} onClick={()=>setTab(key)}>{label}</button>)}</nav><div className="top-search"><span>⌕</span><input aria-label="全库搜索" placeholder="名称、别名或编号" value={query} onChange={e=>{setQuery(e.target.value);setSearchNotice('');}} onKeyDown={e=>e.key==='Enter'&&jumpReference()}/><button className="reference-jump" title="按编号跳转" aria-label="按编号跳转" onClick={jumpReference}>↵</button>{searchNotice?<small className="search-notice" role="status">{searchNotice}</small>:null}</div><button className="connection" onClick={refresh} title="重新读取当前数据"><i className={connection==='书库已连接'?'online':''}/>{connection}</button></header>
 <div className="workbench"><EntityRail data={entities.data} query={query} onQuery={setQuery} kind={kind} onKind={setKind} focus={focus} onEntity={onEntity} offset={entityOffset} onOffset={setEntityOffset}/>
 <main className="workspace"><div className="workspace-toolbar"><div className="view-label">{tab==='graph'?'关系':tab==='facts'?'事实':tab==='scores'?'试评分':'原文'}</div>{(tab==='graph'||tab==='facts')?<><select aria-label="复核状态筛选" value={state} onChange={e=>setState(e.target.value)}>{['active','pending','accepted','rejected','all'].map(s=><option key={s} value={s}>{states[s]}</option>)}</select>{tab==='graph'?<label className="checkbox"><input type="checkbox" checked={events} onChange={e=>setEvents(e.target.checked)}/>展开事件与言语</label>:<select aria-label="事实范围" value={scope} onChange={e=>setScope(e.target.value)}><option value="entity">当前对象</option><option value="all">全库</option><option value="chapter">当前章节</option>{paragraph?<option value="paragraph">{paragraph}</option>:null}</select>}</>:null}<span className="live-tally"><i className="dot pending"/>{status?.review.pending||0} 待核</span></div>
 <div className="main-view">{tab==='scores'?<Scores version={version} onEntity={onEntity} selected={selection.id}/>:tab==='graph'?graph.error?<div className="error">{graph.error}</div>:<Graph data={graph.data} onEntity={onEntity} onFact={onFact} selected={selection.type==='fact'?selection.id:null}/>:tab==='facts'?<Facts resource={factList} onFact={onFact} selected={selection.id} offset={factOffset} onOffset={setFactOffset}/>:<Chapter uid={unit} highlight={highlight} version={version} onParagraph={onParagraph} onChapter={onChapter}/>}</div>
 <Tracks status={status} version={version} unit={unit} onChapter={onChapter}/>
 </main><Inspector selection={selection} resource={detail} onSource={onSource} onEntity={onEntity} onFact={onFact}/></div>
 <div className="statusbar"><span>中文修订快照 · {status?.source_sha256.slice(0,12)||'…'}…</span><span>{status?.counts.entities||'…'} 条目 · {status?.counts.facts||'…'} 事实</span><span>每 5 秒更新</span></div></div>;
}

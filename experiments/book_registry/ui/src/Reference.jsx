import React, {useState} from 'react';

export default function Reference({code}) {
  const [message,setMessage]=useState('');
  if (!code) return null;
  async function copy(e) {
    e.stopPropagation();
    try {await navigator.clipboard.writeText(code); setMessage('已复制');}
    catch {setMessage('请选择编号复制');}
    setTimeout(()=>setMessage(''),1800);
  }
  return <button className="reference" aria-label={`复制编号 ${code}`} title="复制编号，直接在聊天中反馈" onClick={copy}>
    <code>{code}</code><span>{message||'⧉'}</span>
  </button>;
}

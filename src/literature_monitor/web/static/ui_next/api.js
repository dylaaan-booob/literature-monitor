'use strict';
window.uiNext={
  data:null,
  csrf_token:null,
  async request(path,body,options={}) {
    let response;
    try {
      response=await fetch(path,{
        cache:'no-store',...options,
        ...(body!==undefined?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csrf_token:this.csrf_token || this.data?.csrf_token,...body})}:{}),
      });
    } catch {
      throw new Error('无法连接 Literature Monitor Web 服务，请确认 8000 服务仍在运行，然后刷新页面重试。');
    }
    let data;
    try{data=await response.json();}catch{throw new Error(`服务响应异常 (${response.status})，请确认 Web 服务仍在运行。`);}
    if(!response.ok){const error=new Error(data.message || data.error || `请求失败 (${response.status})`);error.state=data;throw error;}
    return data;
  },
  async workspace(){const data=await this.request('/api/ui-next/workspace');this.data=data;this.csrf_token=data.csrf_token;return data;},
  notice(message,details=[],{retryInbox=false}={}) {
    document.getElementById('run-notice').hidden=!message;
    document.getElementById('run-notice-summary').textContent=message;
    const disclosure=document.getElementById('run-notice-details');
    disclosure.hidden=!details.length;disclosure.open=false;
    document.getElementById('run-notice-messages').replaceChildren(...details.map(text=>{
      const line=document.createElement('p');line.textContent=text;return line;
    }));
    document.getElementById('retry-inbox').hidden=!retryInbox;
  },
};
document.getElementById('message-close').addEventListener('click',()=>uiNext.notice(''));

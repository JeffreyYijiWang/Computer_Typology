const ENDPOINT = 'http://127.0.0.1:43128/api/tab';
let pending = Promise.resolve();

async function publish() {
  const config = await chrome.storage.local.get(['token','browser']);
  if(!config.token) return {ok:false,error:'Paste the pairing token from your dashboard first.'};
  const win = await chrome.windows.getLastFocused();
  const browser = config.browser || (navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome');
  let body = {browser,focused:false,private:false,title:'',url:''};
  if(win.focused && win.type === 'normal') {
    const [tab] = await chrome.tabs.query({active:true,windowId:win.id});
    if(tab) {
      body.focused=true;
      body.private=!!tab.incognito;
      if(!body.private) {
        // Do not send paths, queries, fragments, credentials, or non-web URLs.
        try { const url=new URL(tab.url); if(['http:','https:'].includes(url.protocol)) body.url=url.origin; } catch {}
        body.title=tab.title || '';
      }
    }
  }
  try {
    const response = await fetch(ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${config.token}`},body:JSON.stringify(body),signal:AbortSignal.timeout(4000)});
    const result = response.ok ? {ok:true} : {ok:false,error:'Pairing failed. Check the token and save again.'};
    await chrome.storage.local.set({lastStatus:result,lastContact:Date.now()});
    return result;
  } catch {
    const result={ok:false,error:'The local recorder is unavailable. Start Computer Typology, then try again.'};
    await chrome.storage.local.set({lastStatus:result});
    return result;
  }
}
function report() { pending=pending.catch(()=>{}).then(publish).catch(()=>({ok:false,error:'No browser window is available.'}));return pending; }
async function start() { await chrome.alarms.create('typology-heartbeat',{periodInMinutes:0.5});await report(); }
chrome.runtime.onInstalled.addListener(()=>{start();chrome.runtime.openOptionsPage();});
chrome.runtime.onStartup.addListener(start);
chrome.action.onClicked.addListener(()=>chrome.runtime.openOptionsPage());
chrome.tabs.onActivated.addListener(report);
chrome.tabs.onUpdated.addListener((_id,change,tab)=>{if(tab.active && (change.title!==undefined || change.url!==undefined || change.status==='complete'))report();});
chrome.tabs.onRemoved.addListener(report);
chrome.windows.onFocusChanged.addListener(report);
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='typology-heartbeat')report();});
chrome.runtime.onMessage.addListener((message,_sender,reply)=>{if(message.type==='test'){report().then(reply);return true;}});
// Re-establish the alarm when Chrome wakes this service worker.
chrome.alarms.get('typology-heartbeat').then(alarm=>{if(!alarm)start();});

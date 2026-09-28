const $ = (id) => document.getElementById(id);
let settings, selectedDate, shown = 40, lastRows = [], loading = false;
const localDate = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
const duration = (seconds) => { const s = Math.max(0,Math.round(seconds)); return s >= 3600 ? `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m` : s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`; };
const time = (value) => new Date(value * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
function range() { const start = new Date(`${selectedDate}T00:00:00`); const end = new Date(start); end.setDate(end.getDate()+1); return {start:start.getTime()/1000,end:end.getTime()/1000}; }
function params() { return new URLSearchParams(range()); }
async function api(path, body) { const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${settings.token}`},body:JSON.stringify(body)} : {}); if (!response.ok) throw new Error((await response.json()).error || 'Request failed'); return response.json(); }
function el(tag, cls, text) { const node = document.createElement(tag); if(cls) node.className=cls; if(text!==undefined) node.textContent=text; return node; }
function icon(key) { const img = el('img'); img.src=key?`/icons/${key}.png`:'/app-icon.svg'; img.alt=''; return img; }
function renderIntervals() {
  const parent=$('intervals'); parent.replaceChildren();
  if (!lastRows.length) { parent.append(el('p','empty','No activity recorded for this day. Your history will appear as you use your computer.')); return; }
  const bounds=range();
  for (const row of lastRows.slice(0,shown)) {
    const node=el('article','interval'), copy=el('div'), times=el('div','interval-time',duration(row.duration_seconds));
    copy.append(el('h4','',row.app_name));
    if(row.tab_title || row.window_title) copy.append(el('p','title',row.tab_title || row.window_title));
    if(row.domain) copy.append(el('p','title domain',row.domain));
    times.append(el('small','',`${time(Math.max(bounds.start,row.start_epoch))} – ${time(Math.min(bounds.end,row.end_epoch))}`));
    node.append(icon(row.icon_key),copy,times); parent.append(node);
  }
  if(lastRows.length>shown) { const button=el('button','button secondary more-button',`Show more (${lastRows.length-shown} remaining)`); button.onclick=()=>{shown+=40;renderIntervals();}; parent.append(button); }
}
function renderApps(apps) { const parent=$('apps'); parent.replaceChildren(); if(!apps.length) parent.append(el('p','empty','Your most-used apps will appear here.')); const total=apps.reduce((s,a)=>s+a.seconds,0); for(const app of apps){const row=el('div','app-row'),label=el('div','app-label'),track=el('progress','bar-track'); label.append(icon(app.icon_key),el('span','',app.app_name),el('strong','',duration(app.seconds)));track.max=total||1;track.value=app.seconds;track.setAttribute('aria-label',`${app.app_name}: ${duration(app.seconds)}`);row.append(label,track);parent.append(row);} }
async function refresh() {
  if(loading) return; loading=true;
  const requestedDate=selectedDate;
  try {
    const [status,activity]=await Promise.all([api('/api/status'),api(`/api/activity?${params()}`)]);
    if(requestedDate!==selectedDate) return;
    $('error').hidden=true; $('state').textContent=status.state; $('live-dot').classList.toggle('off',status.state!=='Tracking');
    $('username').textContent=status.username; $('avatar').textContent=status.username[0].toUpperCase();
    $('pause').textContent=status.state==='Paused'?'Resume tracking':'Pause tracking'; settings.paused=status.state==='Paused';
    const current=status.current;
    $('live-heading').textContent=current?current.app_name:status.state==='Paused'?'Taking a pause':status.state==='Idle'?'Away from your desk':'Waiting for activity';
    $('live-detail').textContent=current?(current.tab_title||current.window_title||(current.browser?'Connect the browser extension to see the active tab.':'Foreground application')):'Tracking resumes when an eligible application is active.';
    $('live-meta').textContent=current?`${current.domain?current.domain+' · ':''}Since ${time(current.since)} · ${duration(Date.now()/1000-current.since)}`:'';
    $('current-icon').src=current?`/icons/${current.icon_key}.png`:'/app-icon.svg'; $('current-icon').alt=current?current.app_name:'Computer Typology';
    $('active-time').textContent=duration(activity.total_seconds); $('app-count').textContent=activity.apps.length; $('period-count').textContent=activity.intervals.length;
    $('sync-state').textContent=status.sync.state;
    $('sync-detail').textContent=status.sync.last_success?`Last sync ${time(status.sync.last_success)} · ${status.sync.pending} pending`:'History is saved on this computer.';
    $('cloud-description').textContent=status.sync.state==='Connected'?'Connected to PostgreSQL. Your local history is synced automatically.':`PostgreSQL: ${status.sync.state.toLowerCase()}. Activity is saved locally.`;
    lastRows=activity.intervals;renderIntervals();renderApps(activity.apps);
    $('updated').textContent=`Updated ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
  } catch(error) { $('error').hidden=false;$('error').textContent='The recorder is unavailable. Start Computer Typology from the Start menu to reconnect.'; }
  finally { loading=false; }
}
function chooseDate(value){selectedDate=value;$('date').value=value;shown=40;$('next-day').disabled=value>=localDate();$('day-caption').textContent=new Date(`${value}T12:00:00`).toLocaleDateString([],{weekday:'long',month:'long',day:'numeric',year:'numeric'});refresh();}
function shiftDate(days){const date=new Date(`${selectedDate}T12:00:00`);date.setDate(date.getDate()+days);chooseDate(localDate(date));}
$('date').max=localDate();$('date').onchange=()=>{if($('date').value)chooseDate($('date').value);};$('previous-day').onclick=()=>shiftDate(-1);$('next-day').onclick=()=>shiftDate(1);$('today').onclick=()=>chooseDate(localDate());
$('export').onchange=()=>{if($('export').value)window.location.href=`/api/export?${params()}&format=${$('export').value}`;$('export').value='';};
$('pause').onclick=async()=>{try{await api('/api/settings',{paused:!settings.paused});await refresh();}catch(error){$('error').hidden=false;$('error').textContent=error.message;}};
$('settings-nav').onclick=()=>{if(!settings)return;$('capture-titles').checked=settings.capture_window_titles;$('idle').value=String(settings.idle_seconds);$('excluded-apps').value=settings.excluded_apps.join(', ');$('excluded-domains').value=settings.excluded_domains.join(', ');$('settings-error').textContent='';$('settings').showModal();};
$('close-settings').onclick=()=>$('settings').close();$('activity-nav').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
$('copy-token').onclick=async()=>{try{await navigator.clipboard.writeText(settings.token);$('copy-result').textContent='Copied';}catch{$('copy-result').textContent='Clipboard unavailable in this browser.';}};
$('settings-form').onsubmit=async(event)=>{event.preventDefault();try{const split=id=>$(id).value.split(',').map(s=>s.trim()).filter(Boolean);await api('/api/settings',{capture_window_titles:$('capture-titles').checked,idle_seconds:Number($('idle').value),excluded_apps:split('excluded-apps'),excluded_domains:split('excluded-domains')});settings=await api('/api/settings');$('idle-label').textContent=duration(settings.idle_seconds);$('settings').close();refresh();}catch(error){$('settings-error').textContent=error.message;}};
(async()=>{try{settings=await api('/api/settings');$('extension-path').textContent=settings.extension_path;$('idle-label').textContent=duration(settings.idle_seconds);$('zone').textContent=Intl.DateTimeFormat().resolvedOptions().timeZone;chooseDate(localDate());setInterval(refresh,3000);}catch(error){$('error').hidden=false;$('error').textContent='Cannot reach Computer Typology. Start the recorder and reload this page.';}})();

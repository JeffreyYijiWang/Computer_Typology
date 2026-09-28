const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function send({focused=true,incognito=false,url='https://name:password@example.com/private?token=secret#fragment',token='local-token',favIconUrl=null,repeats=1}={}) {
  const sent=[];
  let iconFetches=0;
  const event={addListener(){}};
  const context=vm.createContext({
    URL, AbortSignal, Uint8Array, btoa, navigator:{userAgent:'Edg/140'},
    createImageBitmap:async()=>({close(){}}),
    OffscreenCanvas:class {getContext(){return {drawImage(){}};}async convertToBlob(){return {size:4,arrayBuffer:async()=>Uint8Array.of(1,2,3,4).buffer};}},
    fetch:async (endpoint,options)=>{if(endpoint.startsWith('chrome-extension://')){iconFetches++;return {ok:true,blob:async()=>({size:4})};}sent.push({endpoint,...options,body:JSON.parse(options.body)});return {ok:true};},
    chrome:{
      storage:{local:{get:async()=>({token,browser:'edge'}),set:async()=>{}}},
      windows:{getLastFocused:async()=>({id:1,focused,type:'normal'}),onFocusChanged:event},
      tabs:{query:async()=>[{title:'Example',url,incognito,favIconUrl}],onActivated:event,onUpdated:event,onRemoved:event},
      runtime:{getURL:path=>'chrome-extension://test'+path,onInstalled:event,onStartup:event,onMessage:event,openOptionsPage:async()=>{}},
      alarms:{create:async()=>{},get:async()=>({name:'typology-heartbeat'}),onAlarm:event},
      action:{onClicked:event},
    },
  });
  vm.runInContext(fs.readFileSync('extension/background.js','utf8'),context);
  for(let n=0;n<repeats;n++)await vm.runInContext('report()',context);
  sent.iconFetches=iconFetches;
  return sent;
}
test('focused tab sends an origin, never a full URL or credentials',async()=>{
  const [request]=await send();
  assert.equal(request.body.url,'https://example.com');
  assert.equal(request.body.title,'Example');
  assert.equal(request.body.browser,'edge');
  assert.equal(request.headers.Authorization,'Bearer local-token');
  assert.equal(request.endpoint,'http://127.0.0.1:43128/api/tab');
});
test('unfocused windows and private tabs transmit no tab details',async()=>{
  for(const options of [{focused:false},{incognito:true}]) {
    const [request]=await send(options);
    assert.equal(request.body.title,'');assert.equal(request.body.url,'');
  }
});
test('unpaired extension never sends activity',async()=>assert.equal((await send({token:''})).length,0));
test('non-web URLs are omitted',async()=>assert.equal((await send({url:'file:///C:/private.txt'}))[0].body.url,''));
test('favicons use browser cache once and send bounded PNG data',async()=>{
  const sent=await send({favIconUrl:'https://example.com/favicon.ico',repeats:3});
  assert.equal(sent.iconFetches,1);
  assert.equal(sent[0].body.favicon_png,'AQIDBA==');
  assert.equal(sent[0].body.url,'https://example.com');
});
test('private and background tabs never fetch or transmit favicons',async()=>{
  for(const options of [{focused:false},{incognito:true},{token:''},{url:'file:///private'}]){
    const sent=await send({...options,favIconUrl:'https://example.com/favicon.ico'});
    assert.equal(sent.iconFetches,0);
    assert.ok(!sent[0]?.body.favicon_png);
  }
});

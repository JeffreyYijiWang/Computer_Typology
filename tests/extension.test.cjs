const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

async function send({focused=true,incognito=false,url='https://name:password@example.com/private?token=secret#fragment',token='local-token'}={}) {
  const sent=[];
  const event={addListener(){}};
  const context=vm.createContext({
    URL, AbortSignal, navigator:{userAgent:'Edg/140'},
    fetch:async (endpoint,options)=>{sent.push({endpoint,...options,body:JSON.parse(options.body)});return {ok:true};},
    chrome:{
      storage:{local:{get:async()=>({token,browser:'edge'}),set:async()=>{}}},
      windows:{getLastFocused:async()=>({id:1,focused,type:'normal'}),onFocusChanged:event},
      tabs:{query:async()=>[{title:'Example',url,incognito}],onActivated:event,onUpdated:event,onRemoved:event},
      runtime:{onInstalled:event,onStartup:event,onMessage:event,openOptionsPage:async()=>{}},
      alarms:{create:async()=>{},get:async()=>({name:'typology-heartbeat'}),onAlarm:event},
      action:{onClicked:event},
    },
  });
  vm.runInContext(fs.readFileSync('extension/background.js','utf8'),context);
  await vm.runInContext('report()',context);
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

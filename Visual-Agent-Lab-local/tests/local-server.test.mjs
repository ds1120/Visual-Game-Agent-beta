import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import http from 'node:http';
import {createLocalServer} from '../server.mjs';

const record=()=>({id:randomUUID(),name:'로컬 실험',note:'',createdAt:new Date().toISOString(),config:{fps:60,x:0,y:0,w:100,h:100,interval:.2,size:640,vlInterval:2,gateY:true,gateVL:true,passY:30,passVL:20},assumptions:{baseGpu:10,hudMs:1,yoloMs:20,vlMs:1000,gpuScale:1},measured:{gpu:55,latency:25,fps:60,vl:null}});
async function fixture(t){
  const dir=await mkdtemp(path.join(tmpdir(),'agent-lab-'));
  const dist=path.join(dir,'dist');await mkdir(dist);await writeFile(path.join(dist,'index.html'),'<html>Visual Agent Lab</html>');
  let server,url;
  const start=async()=>{server=createLocalServer({distDirectory:dist,dataDirectory:path.join(dir,'data')});await new Promise(r=>server.listen(0,'127.0.0.1',r));url=`http://127.0.0.1:${server.address().port}`;};
  const stop=()=>new Promise(r=>server.close(r));
  await start();t.after(async()=>{await stop();await rm(dir,{recursive:true,force:true});});
  return {request:(route,options)=>fetch(url+route,options),badHost:()=>new Promise((resolve,reject)=>{http.get(url+'/api/experiments',{headers:{Host:'untrusted.example'}},r=>{r.resume();resolve(r.statusCode);}).on('error',reject);}),restart:async()=>{await stop();await start();},dir};
}
const post=e=>({method:'POST',headers:{'Content-Type':'application/json','x-lab-request':'1'},body:JSON.stringify(e)});
test('built page and persistent save/list/delete work across restart',async t=>{
  const f=await fixture(t),e=record();
  assert.match(await (await f.request('/')).text(),/Visual Agent Lab/);
  assert.equal((await f.request('/api/experiments',post(e))).status,201);
  await f.restart();assert.deepEqual((await (await f.request('/api/experiments')).json()).experiments,[e]);
  assert.equal((await f.request('/api/experiments?id='+e.id,{method:'DELETE',headers:{'x-lab-request':'1'}})).status,200);
  assert.deepEqual(JSON.parse(await readFile(path.join(f.dir,'data/experiments.json'),'utf8')),[]);
});
test('concurrent writes retain every experiment',async t=>{
  const f=await fixture(t),records=Array.from({length:12},record);
  const responses=await Promise.all(records.map(e=>f.request('/api/experiments',post(e))));
  assert.ok(responses.every(r=>r.status===201));
  assert.equal((await (await f.request('/api/experiments')).json()).experiments.length,12);
});
test('invalid ROI, missing write header and external origin are rejected',async t=>{
  const f=await fixture(t),e=record();e.config.x=10;
  assert.equal((await f.request('/api/experiments',post(e))).status,400);
  assert.equal((await f.request('/api/experiments',{method:'POST',body:JSON.stringify(record())})).status,403);
  assert.equal((await f.request('/api/experiments',{headers:{Origin:'https://untrusted.example'}})).status,403);
  assert.equal(await f.badHost(),403);
  assert.deepEqual((await (await f.request('/api/experiments')).json()).experiments,[]);
});
test('source and data files are not served through static routes',async t=>{
  const f=await fixture(t);
  for(const route of ['/server.mjs','/data/experiments.json','/%2e%2e%2fserver.mjs'])assert.ok([403,404].includes((await f.request(route)).status));
});

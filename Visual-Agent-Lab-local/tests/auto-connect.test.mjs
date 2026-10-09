import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setTimeout as sleep} from 'node:timers/promises';
import ts from 'typescript';

// Exercise the same lifecycle implementation that the dashboard imports.
const source=await readFile(new URL('../lib/auto-connect.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {startAutoConnection}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

test('automatically retries a slow failure without concurrent requests, then stops retrying',async t=>{
  let attempts=0,active=0,peak=0,errors=0;
  const states=[];
  let connected;
  const ready=new Promise(resolve=>{connected=resolve;});
  const cancel=startAutoConnection({
    retryMs:5,
    connect:async()=>{
      attempts++;active++;peak=Math.max(peak,active);
      await sleep(15);active--;
      if(attempts===1)throw new Error('Agent not started yet');
      return 'fresh-session';
    },
    onConnected:connected,onError:()=>{errors++;},onConnecting:value=>states.push(value),
  });
  t.after(cancel);
  assert.equal(await ready,'fresh-session');
  await sleep(25);
  assert.equal(attempts,2);assert.equal(peak,1);assert.equal(errors,1);
  assert.deepEqual(states,[true,false,true,false]);
});

test('unmount aborts pending connection and ignores a late successful reply',async()=>{
  let complete,signal,callbacks=0;
  const pending=new Promise(resolve=>{complete=resolve;});
  const cancel=startAutoConnection({
    connect:s=>{signal=s;return pending;},
    onConnected:()=>{callbacks++;},onError:()=>{callbacks++;},onConnecting:()=>{},retryMs:5,
  });
  cancel();assert.equal(signal.aborted,true);
  complete('old-session');await sleep(15);
  assert.equal(callbacks,0);
});

test('unmount cancels retries queued while the Agent is unavailable',async()=>{
  let attempts=0;
  const cancel=startAutoConnection({
    connect:async()=>{attempts++;throw new Error('offline');},
    onConnected:()=>assert.fail('must remain offline'),onError:()=>{},onConnecting:()=>{},retryMs:30,
  });
  await sleep(5);cancel();await sleep(45);
  assert.equal(attempts,1);
});

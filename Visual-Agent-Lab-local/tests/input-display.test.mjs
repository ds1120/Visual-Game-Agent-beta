import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source=await readFile(new URL('../lib/input-display.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}});
const {inputDisplay,latestMouseClick}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));

test('bindings remain visible while paused and active skills appear on their actual key',()=>{
 const state=inputDisplay({bindings:{USE_POTION:'q',CAST_BUFF:'4',DODGE:'space',MOVE:'mouse_left',ATTACK:'mouse_left'},basic_attack_mode:'hold_right',attack_skills:[{id:'s',name:'번개',key:'2',enabled:true},{id:'off',name:'미사용',key:'3',enabled:false}]},true);
 assert.deepEqual(state.configured.get('Q'),['물약']);assert.deepEqual(state.configured.get('4'),['버프']);assert.deepEqual(state.configured.get('SPACE'),['회피']);
 assert.deepEqual(state.configured.get('2'),['번개']);assert.equal(state.configured.has('3'),false);
 assert.deepEqual(state.configured.get('MOUSE_RIGHT'),['공격']);assert.deepEqual(state.configured.get('MOUSE_LEFT'),['이동']);assert.equal(state.pressed.size,0);
});
test('long keyboard movement is marked as sending before a completion event exists',()=>{
 const state=inputDisplay({movement:{mode:'keys',up:'W',down:'S',left:'A',right:'D'},bindings:{MOVE:'mouse_left'},active:{action:'MOVE',binding:'D+w',duration_ms:3000,source:'policy'}},true);
 assert.deepEqual([...state.sending],['D','W']);assert.equal(state.pressed.size,0);assert.equal(state.configured.has('MOUSE_LEFT'),false);assert.deepEqual(state.configured.get('W'),['이동']);
});
test('completed keys expire using elapsed time even if the state stream stalls',()=>{
 const input={events:[{seq:1,status:'sent',action:'USE_SKILL',binding:'2',age_ms:200,duration_ms:40,source:'policy'}]};
 assert.equal(inputDisplay(input,true).pressed.has('2'),true);
 assert.equal(inputDisplay(input,true,50).pressed.has('2'),false);
});
test('blocked and cancelled commands never show successful key presses; offline clears holds',()=>{
 const input={events:[{seq:1,status:'blocked',binding:'Q',age_ms:0},{seq:2,status:'cancelled',binding:'4',age_ms:0}],attack_held:true};
 assert.deepEqual([...inputDisplay(input,true).pressed],['MOUSE_RIGHT']);
 const offline=inputDisplay({...input,active:{binding:'W'}},false);assert.equal(offline.pressed.size,0);assert.equal(offline.sending.size,0);
});
test('last successful input remains visible for older Agents without an event list',()=>{
 const state=inputDisplay({last_sent:{action:'DODGE',binding:'Space',duration_ms:40,source:'policy'},last_sent_age_ms:200},true);
 assert.equal(state.pressed.has('SPACE'),true);
});

test('active and held indicators clear when telemetry stops arriving',()=>{
 const input={active:{binding:'W'},attack_held:true};
 assert.equal(inputDisplay(input,true,749).sending.has('W'),true);
 const stale=inputDisplay(input,true,750);
 assert.equal(stale.sending.size,0);assert.equal(stale.held,false);assert.equal(stale.pressed.size,0);
});

test('releasing a maintained attack clears right mouse immediately despite a recent event',()=>{
 const input={basic_attack_mode:'hold_right',attack_held:false,events:[{seq:1,status:'sent',action:'ATTACK',binding:'mouse_right',age_ms:0}],last_sent:{action:'ATTACK',binding:'mouse_right'},last_sent_age_ms:0};
 assert.equal(inputDisplay(input,true).pressed.has('MOUSE_RIGHT'),false);
 assert.equal(inputDisplay({...input,attack_held:true},true).pressed.has('MOUSE_RIGHT'),true);
});

test('minimap click coordinates use the last successfully sent mouse command, not a request or failure',()=>{
 const click={seq:1,status:'sent',binding:'mouse_left',target:[.7,.3],age_ms:8000};
 const input={requested:{binding:'mouse_left',target:[.1,.1]},events:[click,{seq:2,status:'sent',binding:'Q',target:[.4,.4]},{seq:3,status:'blocked',binding:'mouse_right',target:[.2,.2]}]};
 assert.deepEqual(latestMouseClick(input),click);
 assert.equal(latestMouseClick({events:[{seq:4,status:'sent',binding:'mouse_left',target:[-1,.5]}]}),undefined);
});

test('older Agent last_sent can supply click coordinates without events',()=>{
 assert.deepEqual(latestMouseClick({last_sent:{binding:'mouse_right',target:[.2,.6]},last_sent_age_ms:9000}),{binding:'mouse_right',target:[.2,.6],age_ms:9000});
});

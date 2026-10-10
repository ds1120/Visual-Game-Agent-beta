"use client";

import { useEffect, useState } from "react";

import { Loader2, Save, RefreshCw } from "lucide-react";

import { toast } from "sonner";

import type {CombatSettings,SteeringSettings,MappingSettings} from './gameplay-controls';

type Skill={id:string;name:string;key:string;enabled:boolean;cooldown_ms:number};

type MovementSkill={enabled:boolean;key:string};

type InputSettings={version:number;tap_ms:number;require_foreground:boolean;bindings:Record<string,string>;movement:{mode:"keys"|"click";up:string;down:string;left:string;right:string};movement_skill?:MovementSkill;pointer_smoothing?:boolean;pointer_duration_ms?:number;attack_max_seconds?:number;attack_recheck_seconds?:number;attack_lost_grace_ms?:number;attack_skills?:Skill[];combat?:CombatSettings;disabled_actions?:string[]};

type Region={visible:boolean;bbox:number[]|null;confidence:number};

type MiniMap={enabled:boolean;bbox:number[]|null;player:number[];rotation_degrees:number;walkable_hsv:number[][][];wall_hsv?:number[][][];player_hsv?:number[][][];stuck_seconds?:number;stuck_threshold?:number;mapping?:MappingSettings;dark_floor?:number};

type ClassRule={type:"monster"|"item"|"npc"|"obstacle";relation:"hostile"|"friendly"|"neutral"|"unknown";min_confidence:number};

type Settings={class_rules?:Record<string,ClassRule>;detector?:{enabled:boolean;model_path:string;confidence:number};window_titles?:string[];capture_fps?:number;hud_layout?:"classic"|"bars_hp_sp_mp";profile:string;revisions:Record<string,string>;input:InputSettings;navigation:{version:number;step_ms:number;step_fraction:number;obstacle_margin:number;minimap:MiniMap;steering?:SteeringSettings};buff_region:Region};

const keys=["mouse_left","mouse_right",..."1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ".split(""),"SPACE","SHIFT","CTRL","ALT","UP","DOWN","LEFT","RIGHT"];

const keyNames:Record<string,string>={mouse_left:"마우스 왼쪽",mouse_right:"마우스 오른쪽",SPACE:"Space"};


function KeySelect({value,onChange,keyboardOnly=false}:{value:string;onChange:(value:string)=>void;keyboardOnly?:boolean}){return <select value={value} onChange={e=>onChange(e.target.value)}>{keys.filter(k=>!keyboardOnly||!k.startsWith("mouse_")).map(k=><option key={k} value={k}>{keyNames[k]??k}</option>)}</select>;}

export default function GameSettings({profile,online,captureFps,call}:{profile:string;online:boolean;captureFps?:number;call:(path:string,method?:string,body?:unknown)=>Promise<unknown>}){

 const [data,setData]=useState<Settings|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");

 async function load(){setBusy(true);setError("");try{setData(await call("/v1/game-settings") as Settings);}catch(e){setError(e instanceof Error?e.message:"설정 불러오기 실패");}finally{setBusy(false);}}

 useEffect(()=>{setData(null);if(online)void load();},[profile,online]);

 const updateInput=(patch:Partial<InputSettings>)=>setData(d=>d?{...d,input:{...d.input,...patch}}:d);

 const skills=data?.input.attack_skills??[];

 const movementSkill=data?.input.movement_skill??{enabled:false,key:'SPACE'};

 function changeMovementSkill(patch:Partial<MovementSkill>){updateInput({movement_skill:{...movementSkill,...patch},...(patch.enabled===true?{disabled_actions:(data?.input.disabled_actions??[]).filter(action=>action!=='DODGE')}:{})});}

 function count(value:number){const next=skills.slice(0,value);while(next.length<value){const n=next.length+1;next.push({id:`skill_${Date.now()}_${n}`,name:`공격 스킬 ${n}`,key:String(n<=9?n:0),enabled:true,cooldown_ms:1000});}updateInput({attack_skills:next,...(value>skills.length?{disabled_actions:(data?.input.disabled_actions??[]).filter(a=>a!=='USE_SKILL')}:{})});}

 function changeSkill(index:number,patch:Partial<Skill>){updateInput({attack_skills:skills.map((s,i)=>i===index?{...s,...patch}:s),...(patch.enabled===true?{disabled_actions:(data?.input.disabled_actions??[]).filter(a=>a!=='USE_SKILL')}:{})});}

 async function save(){if(!data)return;setBusy(true);setError("");try{await call("/v1/game-settings","POST",data);toast.success(`${profile} 설정을 저장·적용했습니다. 시작/재개를 누르세요.`);setData(await call("/v1/game-settings") as Settings);}catch(e){setError(e instanceof Error?e.message:"설정 저장 실패");}finally{setBusy(false);}}

 const m=data?.navigation.minimap;

 function mapPatch(patch:Partial<MiniMap>){setData(d=>d?{...d,navigation:{...d.navigation,minimap:{...d.navigation.minimap,...patch}}}:d);}

 function mapROI(i:number,value:number){const b=m?.bbox??[0,0,250,250];const r=[b[0]/10,b[1]/10,(b[2]-b[0])/10,(b[3]-b[1])/10];r[i]=value;mapPatch({bbox:[r[0]*10,r[1]*10,(r[0]+r[2])*10,(r[1]+r[3])*10]});}




 const diablo4=data?.profile==="diablo4";
 return <section className="game-settings" id="game-settings" aria-label="게임별 설정">
 <div className="input-monitor-heading"><p className="live-help">현재 게임에 적용되는 설정만 표시합니다.</p><button className="text-button" disabled={busy||!online} onClick={()=>void load()}><RefreshCw size={14}/>다시 불러오기</button></div>
 {error&&<p className="live-error" role="alert">{error}</p>}
 {!data?<p className="live-help">{busy?"설정을 불러오는 중…":"Agent에 연결하면 설정이 표시됩니다."}</p>:<fieldset disabled={busy||!online} className="game-settings-fields">
 <h4>게임 캡처</h4>
 <label className="setting-row">게임 창 제목<input aria-label="게임 창 제목 설정" value={(data.window_titles??[]).join(", ")} maxLength={500} placeholder="창 제목 일부 · 여러 제목은 쉼표로 구분" onChange={e=>setData({...data,window_titles:e.target.value.split(",").map(v=>v.trim()).filter(Boolean)})}/></label>
 <label className="setting-row">캡처 FPS<input aria-label="캡처 FPS 설정" type="number" min={15} max={120} step={5} value={data.capture_fps??captureFps??60} onChange={e=>setData({...data,capture_fps:Number(e.target.value)})}/></label>
 {!diablo4&&<p className="live-help">이 게임의 이동·전투 기능은 구현 준비 중입니다. 동작 설정은 구현 후 표시됩니다.</p>}
 {diablo4&&<>
 <div className="settings-columns"><div><h4>반복스킬</h4>
 <p className="live-help">Ctrl+1 사냥시작 또는 Ctrl+4 반복스킬에서 사용합니다. 활성화한 키를 설정 간격으로 반복합니다.</p>
 {data.input.disabled_actions?.includes("USE_SKILL")&&<p className="live-error">스킬 입력이 차단되어 있습니다. 사용할 스킬을 활성화하면 차단이 해제됩니다.</p>}
 <label className="setting-row">등록할 스킬 수<select aria-label="공격 스킬 키 개수" value={skills.length} onChange={e=>count(Number(e.target.value))}>{Array.from({length:13},(_,i)=><option key={i} value={i}>{i}개</option>)}</select></label>
 <div className="attack-skill-list">{skills.map((s,i)=><div className="attack-skill-row" key={s.id}>
 <label className="skill-enable"><input type="checkbox" aria-label={(i+1)+"번 공격 스킬 활성화"} checked={s.enabled} onChange={e=>changeSkill(i,{enabled:e.target.checked})}/>사용</label>
 <label>스킬 이름<input aria-label={(i+1)+"번 공격 스킬 이름"} value={s.name} maxLength={80} onChange={e=>changeSkill(i,{name:e.target.value})}/></label>
 <label>입력 키<KeySelect value={s.key} onChange={key=>changeSkill(i,{key})}/></label>
 <label>사용 간격(ms)<input type="number" min={150} max={60000} step={50} value={s.cooldown_ms} onChange={e=>changeSkill(i,{cooldown_ms:Number(e.target.value)})}/></label>
 </div>)}</div></div><div><h4>이동 · 막힘 복구</h4>
 <label className="skill-enable"><input type="checkbox" aria-label="이동 스킬 사용" checked={movementSkill.enabled} onChange={e=>changeMovementSkill({enabled:e.target.checked})}/>이동이 막히면 이동 스킬 사용</label>
 {movementSkill.enabled&&<label className="setting-row">이동 스킬 키<KeySelect value={movementSkill.key} onChange={key=>changeMovementSkill({key})}/></label>}
 <p className="live-help">Ctrl+3 이동에서 사용합니다. 기본 이동은 화면 화살표·미니맵 경로를 따라 마우스 왼버튼을 유지합니다.</p>
 </div></div>
 <details className="settings-advanced"><summary>고급 설정 · 미니맵 보정과 입력 시간</summary><div>
 <h4>미니맵 보정</h4><p className="live-help">게임 해상도나 미니맵 크기가 바뀐 경우에만 조정하세요.</p>
 <div className="binding-grid">{["미니맵 X(%)","미니맵 Y(%)","미니맵 너비(%)","미니맵 높이(%)"].map((label,i)=>{const b=m?.bbox??[0,0,250,250];return <label key={label}>{label}<input type="number" min={0} max={100} step={.1} value={(i<2?b[i]:b[i]-b[i-2])/10} onChange={e=>mapROI(i,Number(e.target.value))}/></label>;})}</div>
 <label className="setting-row">지도 이동 투영 배율<input type="number" min={2} max={40} step={.5} value={m?.mapping?.screen_pixels_per_map_pixel??12} onChange={e=>mapPatch({mapping:{enabled:m?.mapping?.enabled??true,...m?.mapping,screen_pixels_per_map_pixel:Number(e.target.value)}})}/></label>
 <label className="setting-row">벽 가장자리 제외 폭(px)<input type="number" min={0} max={5} step={1} value={m?.mapping?.wall_margin_px??1} onChange={e=>mapPatch({mapping:{enabled:m?.mapping?.enabled??true,...m?.mapping,wall_margin_px:Number(e.target.value)}})}/></label>
 <label className="setting-row">통로 여유 폭(px)<input type="number" min={2} max={30} value={m?.mapping?.preferred_clearance_px??14} onChange={e=>mapPatch({mapping:{enabled:m?.mapping?.enabled??true,...m?.mapping,preferred_clearance_px:Number(e.target.value)}})}/></label>
 <label className="setting-row">키 누름 시간(ms)<input type="number" min={10} max={150} step={5} value={data.input.tap_ms} onChange={e=>updateInput({tap_ms:Number(e.target.value)})}/></label>
 </div></details></>}
 <div className="live-actions"><button className="button primary" disabled={busy} onClick={()=>void save()}>{busy?<Loader2 size={15} className="spin"/>:<Save size={15}/>}설정 저장·적용</button><span className="live-help">저장 시 실행이 중단됩니다.</span></div>
 </fieldset>}</section>;
}

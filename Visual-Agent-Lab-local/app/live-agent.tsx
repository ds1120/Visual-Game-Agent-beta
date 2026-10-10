"use client";

import { useEffect, useRef, useState } from "react";
import { Play, Square, Send, RefreshCw, Radio, Download, Loader2, Swords, Hand, ArrowUp } from "lucide-react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import type { Config, Measurements } from "@/lib/tuning";
import { startAutoConnection } from "@/lib/auto-connect";
import StatisticsPanel from "./statistics-panel";
import GameSettings from "./game-settings";
import GameTabs from "./game-tabs";
import InputMonitor, {type InputTelemetry} from "./input-monitor";
import LearningPanel, {type LearningState} from "./learning-panel";
import MinimapMapping, {type MinimapMappingState} from './minimap-mapping';
import GameScreenPreview from './game-screen-preview';

type AgentEvent = {seq:number;type:string;request_id?:string;reply?:string;message?:string;saved?:string[];directive?:{action:string}};
type AgentState = {
  control_mode?:"manual"|"auto_hunt"|"paused"|"move_only"|"stationary_hunt";learning?:LearningState;recognition_mode?:string;scene?:{type:string;age_seconds:number|null;min_interval:number;stable_interval:number;analysis_status?:string};
  api_version:number;instance:string;profile:string;running:boolean;paused:boolean;foreground:boolean;hud_ready:boolean;capture_fresh:boolean;input_backend:string;restart_required:boolean;
  navigation?:{stuck:boolean;fresh:boolean;minimap_enabled:boolean;player:number[]|null;movement_block_reason?:string|null;mapping?:MinimapMappingState};hud_rechecking?:boolean;hud_recheck_status?:string;input?:InputTelemetry;processing_halted?:boolean;halt_reason?:string|null;hunt_active?:boolean;object_counts?:{total:number;raw:number;monsters:number;items:number;unknown:number};
  hud:{health:number|null;mp:number|null;sp:number|null;missing_visible?:boolean;missing_seconds?:number;calibration_mode?:string};action:string|null;
  buffs?:{configured:boolean;active:string[];known_templates:string[];fresh:boolean;pending_recast:boolean;absence_seconds:number;retry_seconds:number};
  metrics:{gpu:number|null;fps:number|null;hud_ms:number|null;yolo_ms:number|null;latency:number|null;vl:number|null;gated_frames:number};
  settings:Partial<Config>;events:AgentEvent[];
  connection?:{hp_missing_seconds:number;hp_timeout_seconds:number};
  attack?:{ready:number;reason:string;class_rules:Record<string,{type:string;relation:string;min_confidence:number}>};
  detector?:{enabled:boolean;configured_model:string;active_model:string;confidence:number;device:string;raw:number;passed:number;resolved:number;monsters:number;classes:Record<string,string>;error:string|null};
  objects:{track_id:number;memory_id:number|null;type:string;relation:string;status:string;confidence:number;bbox:number[]}[];
};
type Session = {url:string;token:string};
type Chat = {id:string;text:string;reply?:string;state:"pending"|"done"|"error"};
class AgentRequestError extends Error {
  constructor(message:string,public status:number){super(message);}
}
const value = (n:number|null|undefined, unit="", digits=1) => n == null ? "미측정" : `${n.toFixed(digits)}${unit}`;

async function openSession(url:string,signal?:AbortSignal):Promise<Session>{
  const response=await fetch(url+"/v1/session",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({connect:true}),credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",signal:signal??AbortSignal.timeout(5000)});
  if(response.status===404)throw new Error("로컬 Agent 업데이트가 필요합니다.");
  const data=await response.json() as {api_version?:number;token?:string;error?:string};
  if(!response.ok)throw new Error(data.error??`연결 오류 (${response.status})`);
  if(data.api_version!==1||!data.token)throw new Error("로컬 Agent 업데이트가 필요합니다.");
  return {url,token:data.token};
}

async function request<T>(session:Session,path:string,method="GET",body?:unknown,signal?:AbortSignal):Promise<T> {
  // Loopback HTTPS-to-HTTP is potentially trustworthy; current Chrome may ask for LNA permission.
  const options = {method,headers:{Authorization:`Bearer ${session.token}`,...(body === undefined ? {} : {"Content-Type":"application/json"})},body:body === undefined ? undefined : JSON.stringify(body),credentials:"omit",cache:"no-store",referrerPolicy:"no-referrer",signal:signal ?? AbortSignal.timeout(5000)} as RequestInit;
  const result = await fetch(session.url+path,options);
  const data = await result.json() as {error?:string};
  if (!result.ok) throw new AgentRequestError(data.error ?? `연결 오류 (${result.status})`,result.status);
  return data as T;
}

export default function LiveAgent({view,config,onLoadConfig,onMeasurement,onGameChange}:{view:"game"|"settings"|"statistics"|"experiments"|"model";onGameChange:(id:string)=>void;config:Config;onLoadConfig:(settings:Partial<Config>)=>void;onMeasurement:(measured:Measurements,settings:Partial<Config>,profile:string)=>void}) {
  const [address]=useState(()=>typeof document==="undefined"?"http://127.0.0.1:8765":document.querySelector<HTMLMetaElement>('meta[name="visual-agent-url"]')?.content??"http://127.0.0.1:8765");
  const [session,setSession]=useState<Session|null>(null);
  const [state,setState]=useState<AgentState|null>(null);
  const [online,setOnline]=useState(false);
  const [connecting,setConnecting]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [message,setMessage]=useState("");
  const [chats,setChats]=useState<Chat[]>([]);
  const [controlSlot,setControlSlot]=useState<HTMLElement|null>(null);
  useEffect(()=>{setControlSlot(document.getElementById("game-control-slot"));},[]);
  const seen=useRef({instance:"",seq:0});

  useEffect(()=>{
    if(!session)return;
    let active=true;let timer:ReturnType<typeof setTimeout>;
    const abort=new AbortController();
    const poll=async()=>{
      const timeout=AbortSignal.timeout(4000);
      try {
        const data=await request<AgentState>(session,"/v1/state","GET",undefined,AbortSignal.any([abort.signal,timeout]));
        if(!active)return;
        if(data.api_version!==1)throw new Error("Agent 업데이트가 필요합니다.");
        if(seen.current.instance!==data.instance){
          const first=seen.current.instance==="";
          seen.current={instance:data.instance,seq:first?Math.max(0,...data.events.map(e=>e.seq)):0};
          if(!first)setChats(a=>a.map(c=>c.state==="pending"?{...c,state:"error",reply:"Agent가 재시작되어 대화를 취소했습니다."}:c));
        }
        setState(data);setOnline(true);setError("");
        for(const event of data.events.filter(e=>e.seq>seen.current.seq)){
          if(event.type==="chat_result" || event.type==="chat_error" || event.type==="chat_cancelled"){
            setChats(a=>a.map(c=>c.id===event.request_id?{...c,state:event.type==="chat_result"?"done":"error",reply:event.reply??event.message??"처리를 취소했습니다."}:c));
          }
          if(event.type==="connection_lost"||event.type==="processing_halted")toast.warning(event.message);
          // Routine HP recovery is reflected in the status, without repeated toast popups.
          seen.current.seq=Math.max(seen.current.seq,event.seq);
        }
      } catch(e){
        if(!active)return;
        setOnline(false);
        if(e instanceof AgentRequestError&&e.status===410){
          setSession(null);
          setState(null);
          const reason=e.message;
          setError(reason);
          setChats(a=>a.map(c=>c.state==="pending"?{...c,state:"error",reply:reason}:c));
          return;
        }
        if(e instanceof AgentRequestError&&e.status===401){
          setSession(null);setState(null);setError("Agent에 자동으로 다시 연결하고 있습니다.");
          return;
        }
        setError(e instanceof Error && e.message!=="Failed to fetch" ? e.message : "Agent 실행·브라우저의 로컬 네트워크 접근 권한을 확인하세요. 같은 게임 PC에서 접속해야 합니다.");
      }
      if(active)timer=setTimeout(()=>void poll(),250);
    };
    void poll();
    const stopOnClose=()=>{void fetch(session.url+"/v1/control",{method:"POST",headers:{Authorization:`Bearer ${session.token}`,"Content-Type":"application/json"},body:JSON.stringify({action:"stop"}),keepalive:true}).catch(()=>{});};
    window.addEventListener("pagehide",stopOnClose);
    return ()=>{active=false;abort.abort();clearTimeout(timer);window.removeEventListener("pagehide",stopOnClose);};
  },[session]);

  useEffect(()=>{
    if(session)return;
    return startAutoConnection({
      connect:async(signal)=>{
        const url=new URL(address);
        if(!["http:","https:"].includes(url.protocol)||!["localhost","127.0.0.1","[::1]"].includes(url.hostname)||url.username||url.password||url.search||url.hash||url.pathname!=="/")throw new Error("같은 PC의 localhost 연결 주소가 필요합니다.");
        const next=await openSession(url.origin,AbortSignal.any([signal,AbortSignal.timeout(5000)]));
        const data=await request<AgentState>(next,"/v1/state","GET",undefined,AbortSignal.any([signal,AbortSignal.timeout(5000)]));
        if(data.api_version!==1)throw new Error("Agent 업데이트가 필요합니다.");
        return {next,data};
      },
      onConnected:({next,data})=>{
        if(seen.current.instance!==data.instance){
          if(seen.current.instance)setChats(a=>a.map(c=>c.state==="pending"?{...c,state:"error",reply:"Agent가 재시작되어 대화를 취소했습니다."}:c));
          seen.current={instance:data.instance,seq:Math.max(0,...data.events.map(e=>e.seq))};
        }
        setState(data);setOnline(true);setError("");setSession(next);
      },
      onError:(e)=>{setOnline(false);setError(e instanceof Error&&e.message!=="Failed to fetch"?e.message:"로컬 Agent 실행을 기다리고 있습니다.");},
      onConnecting:setConnecting,
    });
  },[address,session]);
  async function control(action:string){
    if(!session)return;
    try {const result=await request<{state:AgentState}>(session,"/v1/control","POST",{action});setState(result.state);toast.success(action==="stop"?"입력을 중단했습니다.":"자동 반응을 재개했습니다. 게임 창이 전경일 때만 입력합니다.");}
    catch(e){toast.error(e instanceof Error?e.message:"제어 실패");}
  }
  async function apply(){
    if(!session)return;
    setBusy(true);
    try {const result=await request<{applied:Partial<Config>;note:string}>(session,"/v1/tuning","POST",{config});onLoadConfig(result.applied);setState(await request<AgentState>(session,"/v1/state"));toast.success("프로필에 저장하고 적용했습니다. 시작/재개를 눌러 실행하세요.");}
    catch(e){toast.error(e instanceof Error?e.message:"설정 적용 실패");}
    finally{setBusy(false);}
  }
  async function send(commandText=message){
    if(!session||!online||!commandText.trim())return;
    const text=commandText.trim(),id=typeof crypto.randomUUID==="function"?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,"0")).join("");
    setChats(a=>[...a,{id,text,state:"pending" as const}].slice(-30));setMessage("");
    try{await request(session,"/v1/chat","POST",{message:text,id});}
    catch(e){setChats(a=>a.map(c=>c.id===id?{...c,state:"error",reply:e instanceof Error?e.message:"전송 실패"}:c));}
  }
  const previousGame=useRef<string|null>(null);
  useEffect(()=>{if(!state?.profile)return;if(previousGame.current!==state.profile){previousGame.current=state.profile;setChats([]);setMessage("");onLoadConfig(state.settings);onGameChange(state.profile);}},[state?.profile]);
  async function gameSelected(id:string){if(!session)return;setChats([]);setMessage("");const next=await request<AgentState>(session,"/v1/state");setState(next);onLoadConfig(next.settings);onGameChange(id);}
  useEffect(()=>{function escape(e:KeyboardEvent){if(e.key==="Escape"&&!e.repeat&&session){e.preventDefault();void request(session,"/v1/control","POST",{action:"stop"}).catch(()=>{});}}window.addEventListener("keydown",escape);return()=>window.removeEventListener("keydown",escape);},[session]);
  return <>{controlSlot&&createPortal(<div className="game-top-controls">        <GameTabs active={state?.profile??""} online={online} call={(path,method,body)=>session?request(session,path,method,body,AbortSignal.timeout(30000)):Promise.reject(new Error("Agent 자동 연결을 기다리고 있습니다."))} onSelected={gameSelected}/>
        <div className="game-run-controls"><button className="button primary" disabled={!online||busy} onClick={()=>void control("start")}><Play size={15}/>시작 / 재개</button><button className="button stop-button" disabled={!online} onClick={()=>void control("stop")}><Square size={15}/>즉시 중단</button></div>
</div>,controlSlot)}<section hidden={view!=="game"} className="panel live-panel" id="game" aria-label="실제 Agent 연결">
    <div className="panel-heading"><h2><Radio size={18}/>게임</h2><span className={`live-badge ${online?"connected":""}`}>{online?state?.control_mode==="manual"?"연결 · 사용자 직접 조작":state?.paused?"연결 · 일시정지":"연결 · 실행 중":connecting?"자동 연결 중":"자동 재연결 대기"}</span></div>
    <div className="live-body">
      <div className="game-workspace">
      <div className="agent-dialogue top-dialogue" id="qwen-dialogue">
        <LearningPanel learning={state?.learning} onAnswer={setMessage} online={online}/>
        <div className="live-status"><span>게임 창 {state?.foreground?"전경":"비활성 · 입력 차단"}</span>{(state?.hud_rechecking||state?.hud_ready||state?.hud.missing_visible)&&<span>HUD {state?.hud_rechecking?"시작 보정 중":state?.hud_ready?"측정 준비":"미측정"}</span>}<span>실행 액션 {state?.action??"없음"}</span><span>사냥 {state?.control_mode==="manual"?"사용자 직접 조작":state?.hunt_active?"자동사냥":"대기"}</span>{state?.restart_required&&<strong>모델·장치·창 변경은 Agent 재시작 필요</strong>}</div>
        <div className="live-metrics">{[{label:"HP",v:online&&state?.hud.health!=null?value(state.hud.health,"%"):online&&state?.hud.missing_visible?"미측정":"—"},{label:"SP / MP",v:`${online&&state?.hud.sp!=null?value(state.hud.sp,"%"):"—"} / ${online&&state?.hud.mp!=null?value(state.hud.mp,"%"):"—"}`},{label:"GPU 전체 부하",v:value(online?state?.metrics.gpu:null,"%")},{label:"캡처",v:value(online?state?.metrics.fps:null," FPS")},{label:"HUD + OpenCV 처리",v:value(online?state?.metrics.latency:null," ms")},{label:"최근 Qwen 응답",v:value(online?state?.metrics.vl:null," ms")}].map(m=><div key={m.label}><span>{m.label}</span><strong>{m.v}</strong></div>)}</div>
        {state?.attack&&(!state.attack.reason.includes("HP")||state.hud.missing_visible)&&<p className="attack-diagnosis" role="status">공격 상태: {state.attack.reason}</p>}
        {state?.recognition_mode==="opencv_qwen"&&(!state.navigation?.movement_block_reason?.includes("HP")||state.hud.missing_visible)&&<p className="live-help" role="status">이동 상태: {state.navigation?.movement_block_reason||"이동 실행 조건 충족 · 적이 있으면 전투 우선"}</p>}
        <div className="profile-json"><strong>OpenCV + Qwen-VL</strong><p>Qwen: {state?.scene?.analysis_status==="stopped"?"사냥 중단 · Qwen 처리 중지":state?.scene?.analysis_status==="foreground_wait"?"게임 창 활성화 대기":state?.scene?.analysis_status==="analyzing"?"장면 분석 중":"대기"}</p><p>장면: {state?.scene?.type??"unknown"} · OpenCV 추적 객체 {online?state?.objects.length??0:0}개</p><p>자동 Qwen 최소 간격 {state?.scene?.min_interval??3}초 · 정적 화면 {state?.scene?.stable_interval??15}초</p><p>공격 가능 {state?.attack?.ready??0} · 역할·관계는 사용자 기억과 최신 화면으로 검증</p>{state?.detector?.error&&<p className="live-error">{state.detector.error}</p>}</div>
        <h3>Qwen-VL과 대화</h3><div className="chat-history" aria-live="polite">{chats.length===0?<p>“사냥 시작해”, “추적 번호 3 몬스터를 공격해”, “물약 기준을 35%로 바꿔”처럼 요청하세요. 사냥 시작은 이동 중 적을 만나면 공격하고, 이동은 공격 없이 진행합니다. 주황색 선·핀을 우선 따라가며, 없으면 미니맵 통로를 탐색합니다.</p>:chats.slice().reverse().map(c=><div className="chat-turn" key={c.id}><p><b>나</b> {c.text}</p><p className={c.state==="error"?"amber-text":""}><b>Qwen</b> {c.reply??"판단 중… 즉시 중단은 계속 사용할 수 있습니다."}</p></div>)}</div><div className="chat-quick-commands" role="group" aria-label="자주 쓰는 게임 명령">{[{label:"사냥시작",text:"사냥 시작해",icon:Play},{label:"사냥중단",text:"사냥 중단해",icon:Square},{label:"이동",text:"이동",icon:ArrowUp},{label:"반복스킬",text:"반복 스킬",icon:RefreshCw},{label:"제자리사냥",text:"제자리 사냥",icon:Swords},{label:"아이템 줍기",text:"주변 아이템을 주워줘",icon:Hand},{label:"예정 진행 방향 변경",text:"예정 진행 방향 변경",icon:RefreshCw}].map(c=><button key={c.label} type="button" disabled={!online} title={`${c.text} · 바로 전송`} aria-label={`${c.label}: ${c.text} 바로 전송`} onClick={()=>{setMessage(c.text);void send(c.text);}}><c.icon size={20} aria-hidden="true"/><span>{c.label}</span></button>)}</div><form onSubmit={e=>{e.preventDefault();void send();}} className="chat-compose"><input aria-label="게임 대화" placeholder="게임 명령 또는 프로필 수정 요청" value={message} maxLength={6000} disabled={!online} onChange={e=>setMessage(e.target.value)}/><button className="button primary" disabled={!online||!message.trim()} type="submit"><Send size={15}/>전송</button></form>
        <InputMonitor input={state?.input} hunting={state?.hunt_active} online={online}/>
</div>
      <div className="controller-column">
      <GameScreenPreview session={session} online={online&&view==="game"} profile={state?.profile}/>
      {!session?<p className="live-intro" role="status">{connecting?<Loader2 size={16} className="spin"/>:null} Agent에 자동 연결합니다. 실행 전이면 기다렸다가 다시 시도합니다.</p>:<>

        {state?.hud_rechecking&&<p className="hud-checking" role="status">Agent 시작 보정 · 창 크기와 HP 색상을 확인하고 있습니다.</p>}
        {state?.processing_halted&&<p className="live-error" role="alert">{state.halt_reason??"모든 작업 일시정지"} · 연결은 유지됩니다. 사냥시작·이동·반복스킬·제자리사냥 버튼 또는 단축키로 재개하세요.</p>}
        {state?.input_backend==="MockInputController"&&<p className="live-error">입력 백엔드가 mock입니다. 실제 이동·공격에는 config.ini의 INPUT backend=esp32_ble 설정이 필요합니다.</p>}
      </>}
        <div id="controller-panel"><h3 className="controller-title">Controller · 이동 및 입력</h3>
        {state?.navigation?.minimap_enabled&&<p className="live-help">미니맵: {state.navigation.fresh?state.navigation.stuck?"이동 정체 · 로컬 경로 복구":"통로 검사 중":"새 측정 대기"}</p>}
        <MinimapMapping input={state?.input} online={online} map={state?.navigation?.mapping?{...state.navigation.mapping,reason:state.navigation.mapping.reason??(state.processing_halted?'processing_halted':!state.foreground?'foreground_wait':!state.capture_fresh?'capture_wait':'waiting')}:undefined}/>



        </div>
      </div>
      </div>
      {online&&state?.hud.missing_visible&&<p className="hud-checking" role="status">HP 미측정 {state.hud.missing_seconds?.toFixed(1)??"2.0"}초 · {state.processing_halted?"처리 중단 · 재개 필요":"OpenCV 재탐색 중"}</p>}
      {error&&<p className="live-error" role="alert">{error} 자동으로 재연결을 시도합니다. 연결이 끊기면 Agent가 8초 내 입력을 중단합니다.</p>}
    </div>
  </section>{view==="settings"&&<section className="panel agent-settings-panel"><div className="panel-heading"><h2>게임별 설정 · {state?.profile??"Agent 연결 대기"}</h2></div><div className="live-body"><GameSettings key={state?.profile} profile={state?.profile??"게임"} online={online} call={(path,method,body)=>session?request(session,path,method,body):Promise.reject(new Error("Agent 자동 연결을 기다리고 있습니다."))}/><fieldset disabled={!online||!session} className="tuning-apply"><div className="live-actions"><button className="button secondary" disabled={!online||busy} onClick={()=>void apply()}>{busy?<Loader2 size={15} className="spin"/>:<RefreshCw size={15}/>}현재 튜닝을 Agent에 적용</button><button className="button secondary" disabled={!online||!state} onClick={()=>state&&onLoadConfig(state.settings)}>Agent 설정 불러오기</button><button className="button secondary" disabled={!online||!state} onClick={()=>state&&onMeasurement({gpu:state.metrics.gpu,latency:state.metrics.latency,fps:state.metrics.fps,vl:state.metrics.vl},state.settings,state.profile)}><Download size={15}/>현재 실측값 가져오기</button></div>
        <p className="live-help">FPS·ROI·OpenCV + Qwen 주기·크기·모션 게이팅·객체 학습 주기를 저장·적용합니다. 적용 후 일시정지합니다. 통과율(%)은 추정 가정입니다. Qwen 객체 게이팅 ON은 미확정 객체만, OFF는 확정 객체도 주기적으로 재검토합니다. GPU는 게임을 포함한 전체 부하, 처리 시간은 최근 OpenCV+OpenCV + Qwen 합산입니다.</p></fieldset></div></section>}{view==="statistics"&&<StatisticsPanel key={state?.profile} session={session} online={online}/>}</>;
}

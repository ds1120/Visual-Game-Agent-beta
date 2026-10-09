"use client";
import {useEffect,useId,useState} from "react";
import "./game-screen-preview.css";

type Preview={regions?:{kind:string;label:string;color:string;source:string}[];screen:string|null;minimap?:string|null;mask?:string|null;reason?:string;age_seconds?:number;stale?:boolean;halted?:boolean;foreground?:boolean;terrain_valid?:boolean;player_walkable?:boolean;walkable_ratio?:number|null;resolution?:number[];viewport?:number[];source?:string};

let previewCache:{key:string;at:number;settled:boolean;pending:Promise<Preview>}|null=null;
function loadPreview(session:{url:string;token:string},profile?:string){
 const key=JSON.stringify([session.url,session.token,profile]);
 if(previewCache?.key===key&&(!previewCache.settled||Date.now()-previewCache.at<650))return previewCache.pending;
 const pending=fetch(session.url+"/v1/preview",{headers:{Authorization:`Bearer ${session.token}`},cache:"no-store",credentials:"omit",referrerPolicy:"no-referrer",signal:AbortSignal.timeout(4000)}).then(async response=>{
  if(!response.ok)throw new Error(response.status===404?"화면 미리보기 적용을 위해 로컬 Agent를 재시작하세요.":`화면을 불러오지 못했습니다 (${response.status}).`);
  return await response.json() as Preview;
 });
 const entry={key,at:Date.now(),settled:false,pending};previewCache=entry;
 const settled=()=>{entry.settled=true;entry.at=Date.now();};void pending.then(settled,settled);return pending;
}

export default function GameScreenPreview({session,online,profile,mode="screen"}:{mode?:"screen"|"minimap";session:{url:string;token:string}|null;online:boolean;profile?:string}){
 const id=useId();
 const [hover,setHover]=useState(false),[focused,setFocused]=useState(false),[pinned,setPinned]=useState(false);
 const [data,setData]=useState<Preview|null>(null),[error,setError]=useState("");
 const inline=mode==="minimap";
 const open=inline||hover||focused||pinned;
 useEffect(()=>{setData(null);setError("");},[session?.url,session?.token,profile]);
 useEffect(()=>{
  setError("");
  if(!open||!session||!online)return;
  let active=true;let timer:ReturnType<typeof setTimeout>|undefined;
 
  async function poll(){
   try{
    const next=await loadPreview(session!,profile);
    if(active){setData(next);setError("");}
   }catch(e){if(active){setError(e instanceof Error?e.message:"화면 확인 실패");}}
   if(active)timer=setTimeout(()=>void poll(),750);
  }
  void poll();
  return()=>{active=false;clearTimeout(timer);};
 },[open,session,online,profile]);
 if(inline)return <figure className="controller-minimap-preview"><figcaption>미니맵 원본 · 초록 십자: 설정된 플레이어 위치</figcaption>{data?.minimap?<img src={data.minimap} alt="미니맵 원본 캡처와 설정된 플레이어 위치"/>:<p role="status">{error||"마지막 미니맵 캡처 대기"}</p>}<p>{data?`마지막 캡처 · ${data.age_seconds??0}초 전${data.stale||!online?' · 이전 측정':''}`:""}</p></figure>;
 return <div className="game-screen-preview" onMouseEnter={()=>setHover(true)} onMouseLeave={()=>setHover(false)} onFocus={()=>setFocused(true)} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setFocused(false);}} onKeyDown={e=>{if(e.key==='Escape'){setPinned(false);setHover(false);setFocused(false);}}}>
  <button type="button" className="button secondary" aria-expanded={open} aria-controls={id} onClick={()=>setPinned(true)}>화면캡처 미리보기</button>
  <span className="live-help">마우스를 올리면 마지막 화면캡처를 표시합니다.</span>
  {open&&<div id={id} className="screen-preview-popup" role="region" aria-label="게임 화면캡처 미리보기">
   <div className="screen-preview-heading"><strong>마지막 화면캡처 / HP·SP·MP / 스킬 / 버프 영역</strong><button className="button secondary" type="button" onClick={()=>{setPinned(false);setHover(false);setFocused(false);}}>미리보기 닫기</button></div>
   {!online?<p>Agent 자동 연결을 기다리고 있습니다.</p>:error?<p className="live-error" role="status">{error}</p>:!data?<p role="status">캡처 화면을 불러오는 중…</p>:!data.screen?<p role="status">{data.reason}</p>:<>
    <p role="status">{data.stale?'마지막 캡처':'실시간 캡처'} · {data.age_seconds??0}초 전 · {data.source} · {data.resolution?.join(' × ')}{data.viewport?` · 게임 영역 ${data.viewport[2]-data.viewport[0]} × ${data.viewport[3]-data.viewport[1]}`:''}{data.halted?' · 처리가 중단된 상태':!data.foreground?' · 게임 창 비활성':''}</p>
    <div className="preview-region-legend"><span style={{color:"#f0f050"}}>미니맵</span>{data.regions?.map((r,i)=><span key={`${r.kind}-${i}`} style={{color:r.color}}>{r.label}{r.source==="profile_default"?" (프로필 기본 영역)":""}</span>)}</div>
    <img className="screen-preview-image" src={data.screen} alt="게임 캡처 화면과 미니맵 분석 영역"/>
    <p className="live-help">{data.minimap?`통로 비율 ${Math.round((data.walkable_ratio??0)*100)}% · ${!data.terrain_valid?'지형 판정 실패 · 미니맵 영역과 색상 설정 확인 필요':data.player_walkable===false?'플레이어 위치가 장애물로 판정됨 · 위치 설정 확인 필요':data.player_walkable===true?'플레이어 위치와 통로 색상 구분됨':'통로 색상 구분됨'}`:'미니맵 분석 영역이 비활성화되어 있습니다.'}</p>
   </>}
  </div>}
 </div>;
}

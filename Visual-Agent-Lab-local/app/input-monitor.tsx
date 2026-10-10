"use client";
import "./controller-input.css";
import {useEffect,useMemo,useState} from "react";
import {inputDisplay,type InputTelemetry} from "@/lib/input-display";
export type {InputTelemetry} from "@/lib/input-display";
const keyboardRows=[['ESC','F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12'],['`','1','2','3','4','5','6','7','8','9','0','-','=','BACKSPACE'],['TAB','Q','W','E','R','T','Y','U','I','O','P','[',']','\\'],['CAPS','A','S','D','F','G','H','J','K','L',';','\'','ENTER'],['SHIFT','Z','X','C','V','B','N','M',',','.','/','SHIFT'],['CTRL','WIN','ALT','SPACE','ALT','CTRL','LEFT','DOWN','UP','RIGHT']];
const keyLabels:Record<string,string>={ESC:'Esc',BACKSPACE:'⌫',TAB:'Tab',CAPS:'Caps',ENTER:'Enter',SHIFT:'Shift',CTRL:'Ctrl',WIN:'Win',ALT:'Alt',SPACE:'Space',LEFT:'←',DOWN:'↓',UP:'↑',RIGHT:'→'};

export default function InputMonitor({input,hunting,online}:{input?:InputTelemetry;hunting?:boolean;online:boolean}){
 const received=useMemo(()=>performance.now(),[input]);
 const [clock,setClock]=useState(()=>performance.now());
 useEffect(()=>{if(!online)return;const timer=setInterval(()=>setClock(performance.now()),50);return()=>clearInterval(timer);},[online]);
 const elapsed=Math.max(0,clock-received);
 const {pressed,sending,configured,held}=inputDisplay(input,online,elapsed);
 const highlighted=(key:string)=>pressed.has(key)||sending.has(key);
 const mouseClass=(key:string)=>sending.has(key)?'mouse-sending':pressed.has(key)?'mouse-pressed':'';
 return <div className="input-monitor">
  <div className="controller-devices"><div className="controller-keyboard" role="group" aria-label="Agent가 전송한 키보드 입력">{keyboardRows.map((row,i)=><div className="controller-key-row" key={i}>{row.map((key,j)=><span key={`${key}-${j}`} data-key={key} className={`controller-key ${key==='SPACE'?'space-key':''} ${configured.has(key)?'configured':''} ${pressed.has(key)?'pressed':''} ${sending.has(key)?'sending':''}`} title={configured.get(key)?.join(' · ')} aria-label={`${keyLabels[key]??key} ${configured.get(key)?.join(' · ')??''}${sending.has(key)?' 전송 중':pressed.has(key)?' 최근 전송':''}`}><b>{keyLabels[key]??key}</b>{configured.has(key)&&<small>{configured.get(key)?.join(' · ')}</small>}</span>)}</div>)}</div><div className="controller-mouse"><svg viewBox="0 0 130 170" role="img" aria-label={`마우스 왼쪽 ${highlighted('MOUSE_LEFT')?'클릭':'대기'}, 오른쪽 ${held?'유지':highlighted('MOUSE_RIGHT')?'클릭':'대기'}`}><path d="M65 8 C25 8 12 35 12 75 L65 75 Z" className={mouseClass('MOUSE_LEFT')}/><path d="M65 8 C105 8 118 35 118 75 L65 75 Z" className={mouseClass('MOUSE_RIGHT')}/><path d="M12 75 H118 V110 C118 170 12 170 12 110 Z"/><rect x="58" y="25" width="14" height="29" rx="6" className={mouseClass('MOUSE_MIDDLE')}/><text x="36" y="60" textAnchor="middle">좌</text><text x="94" y="60" textAnchor="middle">우</text><text x="65" y="116" textAnchor="middle">{held?'우클릭 유지':'마우스'}</text></svg><div className="mouse-bindings"><small>좌: {configured.get('MOUSE_LEFT')?.join(' · ')??'미설정'}</small><small>우: {configured.get('MOUSE_RIGHT')?.join(' · ')??'미설정'}</small></div></div></div>

 </div>;
}

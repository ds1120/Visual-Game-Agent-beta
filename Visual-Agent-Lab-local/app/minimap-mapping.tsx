"use client";
import {useEffect,useRef} from 'react';
import {latestMouseClick,type InputTelemetry} from '@/lib/input-display';

type Journey={goal_id?:number;status:string;measurement_valid:boolean;age_seconds:number;screen_target:number[];click_distance_screen_px:number;map_target:number[]|null;map_start:number[]|null;travelled_map_px:number;displacement_map_px:number;remaining_map_px:number;requested_map_px:number;travel_path:number[][]};
export type MinimapMappingState={planned_direction?:number[]|null;planned_correction?:boolean;pin_target?:number[]|null;planned_source?:string;actual_click_target?:number[]|null;actual_click_start?:number[]|null;valid:boolean;reason?:string;age_seconds?:number|null;walkable_ratio?:number;player_walkable?:boolean;registration:string;segment:number;known_cells:number;visited_cells:number;failed_directions:number;movement_px:number;grid:string[];route:number[][];player:number[];stuck:boolean;recovery_stage?:string;recovery_count?:number;route_clearance_px?:number;planned_target?:number[]|null;goal_locked?:boolean;next_route_ready?:boolean;last_goal_release?:string|null;last_goal_transition?:{goal_id:number;reason:string;remaining_map_px:number}|null;journey?:Journey|null};

export default function MinimapMapping({map,input,online}:{map?:MinimapMappingState;input?:InputTelemetry;online?:boolean}){
 const canvas=useRef<HTMLCanvasElement>(null);
 useEffect(()=>{
  const node=canvas.current;if(!node||!map)return;
  const rows=map.grid,h=rows.length,w=rows[0]?.length??40;node.width=w*8;node.height=(h||30)*8;
  const ctx=node.getContext('2d');if(!ctx)return;
  ctx.fillStyle='#101719';ctx.fillRect(0,0,node.width,node.height);
  const colors:Record<string,string>={'1':'#ac9878','2':'#303638','3':'#477958'};
  rows.forEach((row,y)=>[...row].forEach((cell,x)=>{ctx.fillStyle=colors[cell]??'#101719';ctx.fillRect(x*8,y*8,8,8);}));
  if(map.valid&&map.route.length){ctx.strokeStyle='#8ce8ef';ctx.lineWidth=3;ctx.beginPath();map.route.forEach(([x,y],i)=>{if(i===0)ctx.moveTo(x*node.width,y*node.height);else ctx.lineTo(x*node.width,y*node.height);});ctx.stroke();}
  if(map.pin_target){const [x,y]=map.pin_target;ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.beginPath();ctx.arc(x*node.width,y*node.height-7,4,0,Math.PI*2);ctx.moveTo(x*node.width,y*node.height-3);ctx.lineTo(x*node.width,y*node.height+4);ctx.stroke();}
  const line=(points:number[][],color:string)=>{if(points.length<2)return;ctx.strokeStyle=color;ctx.lineWidth=3;ctx.beginPath();points.forEach(([x,y],i)=>{if(i===0)ctx.moveTo(x*node.width,y*node.height);else ctx.lineTo(x*node.width,y*node.height);});ctx.stroke();};
  const finalTarget=map.valid&&map.route.length>1?(['orange_route','black_arrow'].includes(map.planned_source??'')?map.route[map.route.length-1]:map.route[1]):null;
  if(map.planned_target){const [x,y]=map.planned_target;ctx.strokeStyle='#8ce8ef';ctx.lineWidth=3;ctx.beginPath();ctx.arc(x*node.width,y*node.height,6,0,Math.PI*2);ctx.stroke();}
  if(finalTarget){
   ctx.setLineDash([7,5]);line(['orange_route','black_arrow'].includes(map.planned_source??'')?map.route:[map.player,finalTarget],'#f6e05e');ctx.setLineDash([]);
   const [x,y]=finalTarget;const start=['orange_route','black_arrow'].includes(map.planned_source??'')?map.route[map.route.length-2]:map.player;const angle=Math.atan2((y-start[1])*node.height,(x-start[0])*node.width);
   ctx.fillStyle='#f6e05e';ctx.beginPath();ctx.moveTo(x*node.width,y*node.height);
   ctx.lineTo(x*node.width-10*Math.cos(angle-.5),y*node.height-10*Math.sin(angle-.5));
   ctx.lineTo(x*node.width-10*Math.cos(angle+.5),y*node.height-10*Math.sin(angle+.5));ctx.closePath();ctx.fill();
  }
  if(map.actual_click_target&&map.actual_click_start){
   const target=map.actual_click_target;line([map.actual_click_start,target],'#ffb347');const x=target[0]*node.width,y=target[1]*node.height;ctx.strokeStyle='#ffb347';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x-6,y-6);ctx.lineTo(x+6,y+6);ctx.moveTo(x+6,y-6);ctx.lineTo(x-6,y+6);ctx.stroke();
  }
  if(map.journey){
   line(map.journey.travel_path,'#e879f9');
   const target=map.actual_click_target?null:map.journey.map_target;
   if(target&&map.journey.map_start){line([map.journey.map_start,target],'#ffb347');const x=target[0]*node.width,y=target[1]*node.height;ctx.strokeStyle='#ffb347';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(x-6,y-6);ctx.lineTo(x+6,y+6);ctx.moveTo(x+6,y-6);ctx.lineTo(x-6,y+6);ctx.stroke();}
  }
  const player=rows.length?map.player:[.5,.5];
  if(map.planned_direction){
   const [dx,dy]=map.planned_direction;const px=player[0]*node.width,py=player[1]*node.height;
   const vx=dx*node.width,vy=dy*node.height;const size=Math.hypot(vx,vy);
   if(size>0){
    const angle=Math.atan2(vy,vx),length=Math.min(node.width,node.height)*.22;
    const x=px+vx/size*length,y=py+vy/size*length;
    ctx.strokeStyle='#f6e05e';ctx.lineWidth=4;ctx.setLineDash([7,5]);ctx.beginPath();ctx.moveTo(px,py);ctx.lineTo(x,y);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle='#f6e05e';ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x-12*Math.cos(angle-.5),y-12*Math.sin(angle-.5));ctx.lineTo(x-12*Math.cos(angle+.5),y-12*Math.sin(angle+.5));ctx.closePath();ctx.fill();
   }
  }
  ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(player[0]*node.width,player[1]*node.height,5,0,Math.PI*2);ctx.fill();
 },[map]);
 if(!map)return <div className="profile-json minimap-map"><strong>미니맵 이동 기록</strong><p role="status">미니맵 맵핑 정보 수신 대기</p></div>;
 const click=latestMouseClick(input);
 const journey=map.journey;
 const coords=(point:number[])=>`X ${(point[0]*100).toFixed(1)}% / Y ${(point[1]*100).toFixed(1)}%`;
 const statuses:Record<string,string>={step_refresh:'같은 최종 목표로 연속 클릭',near_goal:'목표 근처 · 다음 지도 확장 방향 선정',step_progress:'실제 이동 확인 · 같은 탐색 목표로 다음 짧은 클릭',moving:'이동 중',approaching:'도착 근처 · 도착 확인 중',obstacle:'새 장애물 · 경로 재검토',goal_blocked:'벽·통로 단절 · 다른 방향 목표 선택',epoch_changed:'명령 변경',segment_changed:'지도 구간 변경',input_failed:'입력 실패',arrived:'최종 목표 도착',waypoint_arrived:'중간 지점 도착 · 같은 목표로 이동',stalled:'같은 목표의 우회 경로 재확인',interrupted:'전투·중단 등으로 이동 종료',map_lost:'지도 연결 실패 · 측정 중단'};
 const button=click?.binding?.toLowerCase().includes('mouse_right')?'우클릭':click?.binding?.toLowerCase().includes('mouse_middle')?'휠 클릭':'좌클릭';
 const recovery:Record<string,string>={none:'중앙 경로 이동',recenter:'통로 중앙 복귀',backtrack:'지나온 길로 후퇴',detour:'다른 통로 탐색',blocked:'복구 실패 · 이동 보류'};
 const reasons:Record<string,string>={waiting:'첫 미니맵 캡처 대기',processing_halted:'HP 미검출로 처리 중단 · 재개 필요',foreground_wait:'게임 창 활성화 대기',capture_wait:'최신 게임 캡처 대기',disabled:'미니맵 비활성화',no_roi:'미니맵 영역 확인 필요',uncalibrated:'미니맵 보정 필요',registration_wait:'지도 스크롤 연결 재확인 중',terrain_invalid:'지형 판정 실패 · 영역과 색상 확인 필요',player_blocked:'플레이어 위치가 장애물로 판정됨 · 위치와 색상 확인 필요',stale:'지도 갱신 지연',processing_error:'미니맵 처리 오류 · 재시도 중'};
 return <div className="profile-json minimap-map"><strong>미니맵 이동 기록</strong><p role="status">{map.valid?(map.stuck?'이동 정체 · 막힌 방향 우회':'통로 검사 · 이동 기록 반영'):reasons[map.reason??'waiting']??'최신 미니맵 확인 대기'} · 지도 구간 {map.segment}</p>{true&&<><canvas ref={canvas} style={{display:'block',width:'100%',maxWidth:384,opacity:map.valid?1:.4,imageRendering:'pixelated'}} aria-label="미니맵 통로, 장애물, 이동 기록과 계획 경로" role="img"/>{!map.valid&&<p className="amber-text">이전 확인 지도 · 현재 이동에는 사용하지 않습니다{map.age_seconds!=null?` · ${map.age_seconds}초 전`:''}</p>}</>}<p>베이지: 통로 · 진회색: 장애물 · 초록: 지나온 길 · 흰점: 플레이어</p>
 <p role="status">노란 화살표: 예정 진행 방향 · 흰점: 캐릭터 위치{map.planned_correction?' · 뒤쪽 화살표를 무시하고 방향 보정 중':''}{!map.grid.length?' · 지도 수신 전 방향 표시':''}</p>
 <div style={{display:'grid',gap:8}}>
  <div><strong style={{color:'#8ce8ef'}}>① 예정 진행 방향 · 하늘색 경로</strong><p>{map.planned_target?`미니맵 최종 목표 ${coords(map.planned_target)}${map.goal_locked?' · 목표 도착까지 유지':''}`:'현재 검증된 방향 없음'}</p><p>이동 버튼은 캐릭터 위치에서 이미지 매칭 화살표 위치로 향하는 각도를 계산해, 화살표보다 50~100픽셀 앞에 커서를 두고 왼버튼을 유지합니다.</p>{map.next_route_ready&&<p>다음 경로 준비됨 · 도착 확인 전 클릭 보류</p>}</div>
  <div><strong style={{color:'#f6e05e'}}>② 예정 진행 경로 · 노란색 점선</strong><p>화살표 모양의 방향은 읽지 않습니다. 캐릭터와 화살표의 위치를 잇는 연장선으로 이동합니다.</p></div>
  <div><strong style={{color:'#ffb347'}}>③ 실제 이동 방향(클릭) · 주황색 선과 ×</strong><p>{journey?`이동 목표 ${journey.goal_id!=null?`#${journey.goal_id} · `:''}전송한 클릭: 화면 ${coords(journey.screen_target)} · 클릭 거리 ${journey.click_distance_screen_px}px`:'전송한 이동 클릭 없음'}</p>{journey?.map_target&&<p>전송 당시 위치에서 클릭 위치까지 표시 · 미니맵 투영 목표(추정): {coords(journey.map_target)}</p>}<p aria-label="최근 마우스 클릭 좌표">최근 마우스 입력: {click?.target?`${coords(click.target)} · ${button}${!online?' · 마지막 연결 기록':''}`:'없음'}</p></div>
  <div><strong style={{color:'#e879f9'}}>④ 측정된 이동거리 · 분홍색 궤적</strong><p>{journey?`이동 ${journey.travelled_map_px.toFixed(1)} 지도 px · 시작점 대비 ${journey.displacement_map_px.toFixed(1)} 지도 px · 남은 거리 ${journey.remaining_map_px.toFixed(1)} 지도 px`:'이동 측정 대기'}</p>{journey&&<p>{statuses[journey.status]??journey.status} · 클릭 후 {journey.age_seconds.toFixed(1)}초{!online||!journey.measurement_valid?' · 마지막 측정 기록':''}</p>}{map.last_goal_transition&&<p>목표 #{map.last_goal_transition.goal_id} 최근 경로 상태: {statuses[map.last_goal_transition.reason]??map.last_goal_transition.reason} · 남은 {map.last_goal_transition.remaining_map_px.toFixed(1)} 지도 px</p>}<p>이동거리는 미니맵 위치 변화로 측정합니다. 게임 내 미터 단위가 아닙니다.</p></div>
 </div><p>이동: {map.valid?recovery[map.recovery_stage??'none']??map.recovery_stage:'지도 확인 후 재개'} · 복구 시도 {map.recovery_count??0} · 경로 벽 여유 {map.route_clearance_px??0}px</p><p>기억한 칸 {map.known_cells} · 지나온 칸 {map.visited_cells} · 막힌 방향 {map.failed_directions}</p><p className="live-help">지도는 실행 중 누적합니다. 위치 연결 실패 시 이전 겹치는 지형으로 복구하며, 연결할 수 없는 지도는 별도 구간으로 보관합니다.</p></div>;
}

"use client";
export type LearningState={pending:Array<{id:number;name:string;clues:string;question:string;image:string|null}>;confirmed:Array<{id:number;name:string;kind:string;relation:string;notes:string}>};
export default function LearningPanel({learning,onAnswer,online}:{learning?:LearningState;onAnswer:(text:string)=>void;online:boolean}){
 if(!learning?.pending.length)return null;
 return <section className="profile-json" style={{maxHeight:360,marginBottom:18}} aria-label="객체 정보 확인"><h3>Qwen이 객체 정보를 묻고 있습니다</h3><p>확인한 정보는 현재 게임의 파일에 저장되어 다음 실행에도 사용됩니다.</p>{learning.pending.map(q=><div key={q.id} style={{display:"flex",gap:14,marginTop:14,alignItems:"center"}}>{q.image&&<img src={q.image} width={96} height={96} alt={`${q.name||"이름 미확인"} · 객체 #${q.id}의 확인 요청 이미지`}/>}<div><h4>{q.name?.trim() ? `Qwen이 읽은 이름: ${q.name}` : "이름 미확인"} · 객체 #{q.id}</h4><strong>{q.question}</strong><p>{q.clues}</p><button className="button secondary" disabled={!online} onClick={()=>onAnswer(`학습 ${q.id} ${q.name||"이름"} NPC 아군`)}>답변 양식 넣기</button></div></div>)}</section>;
}

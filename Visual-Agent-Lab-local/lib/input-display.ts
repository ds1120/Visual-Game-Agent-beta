export type InputCommand={action:string;source:string;reason?:string;direction?:number[]|null;target?:number[]|null;track_id?:number|null;duration_ms:number;skill_id?:string|null;skill_name?:string|null;binding?:string|null};
export type InputTelemetry={basic_attack_mode?:string;attack_held?:boolean;requested?:InputCommand|null;active?:InputCommand|null;last_sent?:InputCommand|null;last_sent_age_ms?:number|null;error?:string|null;bindings?:Record<string,string>;movement?:{mode:string;up:string;down:string;left:string;right:string};attack_skills?:Array<{id:string;name:string;key:string;enabled:boolean}>;events?:Array<InputCommand&{seq:number;status:string;age_ms:number;error?:string|null}>};
export const HIGHLIGHT_MS=250;
const roles:Record<string,string>={MOVE:'이동',ATTACK:'공격',USE_SKILL:'스킬',CAST_BUFF:'버프',DODGE:'회피',USE_POTION:'물약',TAKE:'수집',INTERACT:'대화'};
export function bindingKeys(binding?:string|null){
  const aliases:Record<string,string>={CONTROL:'CTRL',ESCAPE:'ESC',RETURN:'ENTER',SPACEBAR:'SPACE',ARROWLEFT:'LEFT',ARROWRIGHT:'RIGHT',ARROWUP:'UP',ARROWDOWN:'DOWN'};
  return (binding??'').split('+').map(k=>k.trim().toUpperCase()).filter(Boolean).map(k=>aliases[k]??k);
}
export function inputDisplay(input:InputTelemetry|undefined,online:boolean,elapsedMs=0){
  const configured=new Map<string,string[]>();
  function add(binding:string|undefined,label:string){for(const key of bindingKeys(binding))configured.set(key,[...new Set([...(configured.get(key)??[]),label])]);}
  for(const [action,key] of Object.entries(input?.bindings??{})){
    if(action==='MOVE'&&input?.movement?.mode==='keys')continue;
    if(roles[action])add(action==='ATTACK'&&input?.basic_attack_mode==='hold_right'?'mouse_right':key,roles[action]);
  }
  if(input?.movement?.mode==='keys')for(const direction of ['up','down','left','right'] as const)add(input.movement[direction],'이동');
  for(const skill of input?.attack_skills??[])if(skill.enabled)add(skill.key,skill.name);
  const events=input?.events??[];
  const fresh=online&&elapsedMs<750;
  const held=!!(fresh&&input?.attack_held);
  const recent=online?events.filter(e=>e.status==='sent'&&e.age_ms+elapsedMs<HIGHLIGHT_MS&&!(e.action==='ATTACK'&&input?.basic_attack_mode==='hold_right')):[];
  const pressed=new Set(recent.flatMap(e=>bindingKeys(e.binding)));
  if(online&&!(input?.last_sent?.action==='ATTACK'&&input?.basic_attack_mode==='hold_right')&&(input?.last_sent_age_ms??Infinity)+elapsedMs<HIGHLIGHT_MS)for(const key of bindingKeys(input?.last_sent?.binding))pressed.add(key);
  if(held)pressed.add('MOUSE_RIGHT');
  const sending=new Set(fresh?bindingKeys(input?.active?.binding):[]);
  return {configured,pressed,sending,recent,events,held};
}

export function latestMouseClick(input?:InputTelemetry){
  const valid=(c:InputCommand)=>c.target?.length===2&&c.target.every(n=>Number.isFinite(n)&&n>=0&&n<=1)&&bindingKeys(c.binding).some(k=>k.startsWith('MOUSE_'));
  const event=[...(input?.events??[])].reverse().find(e=>e.status==='sent'&&valid(e));
  if(event)return event;
  if(input?.last_sent&&valid(input.last_sent))return {...input.last_sent,age_ms:input.last_sent_age_ms??0};
  return undefined;
}

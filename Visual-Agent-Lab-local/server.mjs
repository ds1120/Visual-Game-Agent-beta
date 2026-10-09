import http from 'node:http';
import {readFile,writeFile,mkdir,rename,realpath} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';

const root=path.dirname(fileURLToPath(import.meta.url));
const json=(response,data,status=200)=>{response.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});response.end(JSON.stringify(data));};
const fail=(message,status=400)=>Object.assign(new Error(message),{status});
const finite=(value,min,max)=>typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max;
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export function validateExperiment(e){
  const c=e?.config,a=e?.assumptions,m=e?.measured;
  if(!uuid(e?.id)||typeof e.name!=='string'||!e.name.trim()||e.name.trim().length>80||typeof e.note!=='string'||e.note.length>500||typeof e.createdAt!=='string'||!/^\d{4}-\d{2}-\d{2}T/.test(e.createdAt)||!Number.isFinite(Date.parse(e.createdAt))||!c||!a||!m)throw fail('실험 입력 형식을 확인하세요.');
  for(const [k,min,max] of [['fps',15,120],['x',0,90],['y',0,90],['w',10,100],['h',10,100],['interval',.05,2],['vlInterval',.5,10],['passY',5,100],['passVL',5,100]])if(!finite(c[k],min,max))throw fail('튜닝 범위를 확인하세요.');
  if(![320,448,640,960].includes(c.size)||typeof c.gateY!=='boolean'||typeof c.gateVL!=='boolean'||c.x+c.w>100||c.y+c.h>100)throw fail('ROI 또는 게이팅 설정을 확인하세요.');
  for(const [k,min,max] of [['baseGpu',0,100],['hudMs',0,100],['yoloMs',.1,500],['vlMs',1,60000],['gpuScale',.1,3]])if(!finite(a[k],min,max))throw fail('추정 가정값 범위를 확인하세요.');
  for(const [k,max] of [['gpu',100],['latency',60000],['fps',240],['vl',120000]])if(m[k]!==null&&!finite(m[k],0,max))throw fail('실측 범위를 확인하세요.');
  return {...e,name:e.name.trim()};
}
async function body(request){
  if(Number(request.headers['content-length'])>60000)throw fail('데이터가 너무 큽니다.',413);
  return new Promise((resolve,reject)=>{let length=0,chunks=[];request.on('data',chunk=>{length+=chunk.length;if(length<=60000)chunks.push(chunk);});request.on('end',()=>{if(length>60000)return reject(fail('데이터가 너무 큽니다.',413));try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{reject(fail('JSON 입력을 확인하세요.'));}});request.on('error',reject);});
}

export function createLocalServer({dataDirectory=path.join(root,'data'),distDirectory=path.join(root,'dist'),devHandler=null}={}){
  const dataFile=path.join(dataDirectory,'experiments.json');let mutations=Promise.resolve();
  const load=async()=>{try{const records=JSON.parse(await readFile(dataFile,'utf8'));if(!Array.isArray(records))throw Error('실험 파일 형식 오류');return records;}catch(e){if(e.code==='ENOENT')return [];throw e;}};
  const save=async records=>{await mkdir(dataDirectory,{recursive:true});const temp=path.join(dataDirectory,`.experiments-${randomUUID()}.tmp`);await writeFile(temp,JSON.stringify(records,null,2)+'\n','utf8');await rename(temp,dataFile);};
  const mutate=fn=>{const result=mutations.then(fn);mutations=result.catch(()=>{});return result;};
  return http.createServer(async(request,response)=>{
    try{
      const host=new URL('http://'+request.headers.host);
      if(!['127.0.0.1','localhost','[::1]'].includes(host.hostname)||Number(host.port||80)!==request.socket.localPort)return json(response,{error:'허용되지 않은 요청 주소입니다.'},403);
      if(request.headers.origin){const origin=new URL(request.headers.origin);if(!['127.0.0.1','localhost','[::1]'].includes(origin.hostname)||Number(origin.port||80)!==request.socket.localPort||origin.protocol!=='http:')return json(response,{error:'허용되지 않은 출처입니다.'},403);}
      const url=new URL(request.url,host.origin);
      if(url.pathname==='/api/experiments'){
        if(request.method==='GET'){await mutations;return json(response,{experiments:(await load()).filter(e=>!url.searchParams.get("game")||(e.game??"diablo4")===url.searchParams.get("game")).sort((a,b)=>b.createdAt.localeCompare(a.createdAt))});}
        if(request.headers['x-lab-request']!=='1')return json(response,{error:'요청이 허용되지 않습니다.'},403);
        if(request.method==='POST'){const experiment=validateExperiment(await body(request));await mutate(async()=>{const records=await load(),index=records.findIndex(e=>e.id===experiment.id);if(index<0)records.push(experiment);else records[index]=experiment;await save(records);});return json(response,{experiment},201);}
        if(request.method==='DELETE'){const id=url.searchParams.get('id');if(!uuid(id))throw fail('잘못된 실험 ID입니다.');await mutate(async()=>save((await load()).filter(e=>e.id!==id)));return json(response,{ok:true});}
        return json(response,{error:'지원하지 않는 메서드입니다.'},405);
      }
      if(devHandler){devHandler(request,response);return;}
      if(request.method!=='GET'&&request.method!=='HEAD')return json(response,{error:'지원하지 않는 메서드입니다.'},405);
      const relative=decodeURIComponent(url.pathname);const file=path.resolve(distDirectory,'.'+(relative==='/'?'/index.html':relative));
      if(!file.startsWith(path.resolve(distDirectory)+path.sep))return json(response,{error:'잘못된 경로입니다.'},403);
      let data;try{const resolved=await realpath(file);if(!resolved.startsWith(path.resolve(distDirectory)+path.sep))throw fail('잘못된 경로입니다.',403);data=await readFile(resolved);}catch(e){if(e.code==='ENOENT')return json(response,{error:'파일을 찾을 수 없습니다. npm run build로 빌드하세요.'},404);throw e;}
      const type={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'}[path.extname(file)]||'application/octet-stream';
      response.writeHead(200,{'Content-Type':type,'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});response.end(request.method==='HEAD'?undefined:data);
    }catch(e){if(!response.headersSent)json(response,{error:e.status?e.message:'저장 또는 조회에 실패했습니다. data 폴더와 콘솔을 확인하세요.'},e.status||503);else response.end();if(!e.status)console.error(e);}
  });
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const port=Number(process.env.PORT||3000);if(!Number.isInteger(port)||port<1024||port>65535)throw Error('PORT는 1024~65535 정수여야 합니다.');
  let vite;
  if(process.argv.includes('--dev')){const {createServer}=await import('vite');vite=await createServer({root,server:{middlewareMode:true},appType:'spa'});}
  const server=createLocalServer({devHandler:vite?.middlewares});
  server.on('error',e=>{console.error(e.code==='EADDRINUSE'?'3000 포트를 사용 중입니다. 기존 서버를 종료하거나 PORT를 변경하세요.':e.message);process.exitCode=1;});
  server.listen(port,'127.0.0.1',()=>console.log(`Visual Agent Lab: http://127.0.0.1:${port}/#game\n실험 저장: ${path.join(root,'data','experiments.json')}\n종료: Ctrl+C`));
  const close=()=>server.close(async()=>{await vite?.close();process.exit(0);});process.on('SIGINT',close);process.on('SIGTERM',close);
}

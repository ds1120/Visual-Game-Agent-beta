import { z } from "zod";
export const configSchema = z.object({
  fps:z.number().min(15).max(120), x:z.number().min(0).max(90), y:z.number().min(0).max(90),
  w:z.number().min(10).max(100), h:z.number().min(10).max(100),
  interval:z.number().min(.05).max(2), size:z.union([z.literal(320),z.literal(448),z.literal(640),z.literal(960)]),
  vlInterval:z.number().min(.5).max(10), gateY:z.boolean(),gateVL:z.boolean(),
  passY:z.number().min(5).max(100),passVL:z.number().min(5).max(100),
}).refine(c=>c.x+c.w<=100&&c.y+c.h<=100,{message:"ROI가 화면 경계를 벗어났습니다."});
export const assumptionsSchema=z.object({baseGpu:z.number().min(0).max(100),hudMs:z.number().min(0).max(100),yoloMs:z.number().min(.1).max(500),vlMs:z.number().min(1).max(60000),gpuScale:z.number().min(.1).max(3)});
export const measurementsSchema=z.object({gpu:z.number().min(0).max(100).nullable(),latency:z.number().min(0).max(60000).nullable(),fps:z.number().min(0).max(240).nullable(),vl:z.number().min(0).max(120000).nullable()});
export const experimentSchema=z.object({game:z.string().regex(/^[a-z0-9_]{1,60}$/).optional(),id:z.string().uuid(),name:z.string().trim().min(1).max(80),note:z.string().max(500),config:configSchema,assumptions:assumptionsSchema,measured:measurementsSchema,createdAt:z.string().datetime()});
export type Config=z.infer<typeof configSchema>;
export type Assumptions=z.infer<typeof assumptionsSchema>;
export type Measurements=z.infer<typeof measurementsSchema>;
export type Experiment=z.infer<typeof experimentSchema>;
export const defaultConfig:Config={fps:60,x:15,y:15,w:70,h:65,interval:.3,size:448,vlInterval:1,gateY:true,gateVL:true,passY:65,passVL:22};
export const defaultAssumptions:Assumptions={baseGpu:14,hudMs:3,yoloMs:18,vlMs:1400,gpuScale:1};
export const emptyMeasurements:Measurements={gpu:null,latency:null,fps:null,vl:null};
export const presets:{name:string;config:Config}[]=[
 {name:"균형",config:defaultConfig},
 {name:"저부하",config:{...defaultConfig,size:320,interval:.6,vlInterval:2.5,passY:50,passVL:15}},
 {name:"고빈도",config:{...defaultConfig,size:640,interval:.1,vlInterval:.5,gateY:false,gateVL:false}},
];
export function estimate(c:Config,a:Assumptions){
 const area=c.w*c.h/10000;
 // YOLO letterboxes to a fixed tensor. Cropping changes preprocessing, not tensor FLOPs.
 const yolo=a.yoloMs*(c.size/448)**2;
 const preprocess=1.2*area/.455;
 const fast=a.hudMs+yolo+preprocess;
 const rateY=Math.min(c.fps,1/c.interval)*(c.gateY?c.passY/100:1);
 const rateVL=Math.min(c.fps,1/c.vlInterval)*(c.gateVL?c.passVL/100:1);
 const capture=.07*c.fps;
 const yGpu=yolo*rateY/10*a.gpuScale;
 const vGpu=a.vlMs*rateVL/10*a.gpuScale;
 const rawGpu=a.baseGpu+capture+yGpu+vGpu;
 const gpu=Math.min(100,rawGpu);
 return {gpu,rawGpu,fast,yolo,preprocess,rateY,rateVL,capture,yGpu,vGpu,area,
   waitY:c.interval*1000/2,vl:a.vlMs,frame:1000/c.fps,overload:rawGpu>=100,
   vlBusy:a.vlMs*rateVL/1000};
}
export const targetStatus=(gpu:number,latency:number,fps:number)=>({gpu:gpu<=60,latency:latency<=30,fps:fps>=60});

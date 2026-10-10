#pragma once
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

struct Command { uint32_t id=0; const char* op=nullptr; int a=0,b=0,c=0; };
inline bool keyAllowed(int key) { return key==0 || (key>=4 && key<=39) || key==44 || (key>=79 && key<=82) || (key>=224 && key<=226); }
inline bool parseCommand(char* line,Command& c) {
  char* save=nullptr;char* token=strtok_r(line," ", &save);
  if(!token || strcmp(token,"VGA_BLE_1")) return false;
  token=strtok_r(nullptr," ",&save);if(!token || *token<'1' || *token>'9')return false;
  char* end=nullptr;unsigned long id=strtoul(token,&end,10);if(*end || id>2000000000UL)return false;c.id=id;
  c.op=strtok_r(nullptr," ",&save);if(!c.op)return false;
  int count=(!strcmp(c.op,"KEY"))?3:(!strcmp(c.op,"CLICK")||!strcmp(c.op,"MOVE")||!strcmp(c.op,"HOLD"))?2:0;
  if(!count && strcmp(c.op,"STOP") && strcmp(c.op,"STATUS") && strcmp(c.op,"RELEASE") && strcmp(c.op,"END_TAP"))return false;
  int* args[]={&c.a,&c.b,&c.c};
  for(int i=0;i<count;i++){token=strtok_r(nullptr," ",&save);if(!token)return false;long v=strtol(token,&end,10);if(*end||v < -127||v>(!strcmp(c.op,"HOLD")?1500:400))return false;*args[i]=(int)v;}
  if(strtok_r(nullptr," ",&save))return false;
  if(!strcmp(c.op,"KEY"))return c.a>=10 && c.a<=400 && c.b && keyAllowed(c.b) && keyAllowed(c.c);
  if(!strcmp(c.op,"CLICK"))return c.a>=10 && c.a<=400 && (c.b==1 || c.b==2);
  if(!strcmp(c.op,"MOVE"))return c.a>=-127 && c.a<=127 && c.b>=-127 && c.b<=127;
  if(!strcmp(c.op,"HOLD"))return (c.a==1 || c.a==2) && c.b>=200 && c.b<=1500;
  return true;
}
struct HIDSink {
  virtual void keyboard(const uint8_t* bytes)=0;
  virtual void mouse(uint8_t buttons,int8_t x,int8_t y)=0;
  virtual ~HIDSink()=default;
};
struct HIDState {
  bool held=false;uint32_t began=0,lastCommand=0;int duration=0;
  uint8_t attackButtons=0,tapButtons=0;uint32_t attackBegan=0;int attackLease=0;
  void endTap(HIDSink& sink){const uint8_t empty[8]={0};sink.keyboard(empty);tapButtons=0;sink.mouse(attackButtons,0,0);held=false;}
  void releaseAttack(HIDSink& sink){attackButtons=0;sink.mouse(tapButtons,0,0);}
  void release(HIDSink& sink){attackButtons=0;endTap(sink);}
  void tick(uint32_t now,bool ready,HIDSink& sink){
    if(!ready){if(held || attackButtons)release(sink);return;}
    if(attackButtons && now-attackBegan>=(uint32_t)attackLease)releaseAttack(sink);
    if(held && (now-began>=(uint32_t)duration || now-lastCommand>=500))endTap(sink);
  }
  const char* execute(const Command& c,uint32_t now,bool ready,HIDSink& sink){
    tick(now,ready,sink);
    if(!strcmp(c.op,"STOP")){release(sink);return nullptr;}
    if(!strcmp(c.op,"STATUS"))return nullptr;
    if(!strcmp(c.op,"RELEASE")){releaseAttack(sink);return nullptr;}
    if(!strcmp(c.op,"END_TAP")){endTap(sink);return nullptr;}
    if(!ready)return "BLE_NOT_READY";
    if(!strcmp(c.op,"HOLD")){attackButtons=(uint8_t)c.a;attackBegan=now;attackLease=c.b;sink.mouse(attackButtons|tapButtons,0,0);return nullptr;}
    if(held)return "BUSY";
    lastCommand=now;
    if(!strcmp(c.op,"MOVE")){sink.mouse(attackButtons,c.a,c.b);return nullptr;}
    if(!strcmp(c.op,"CLICK")){
      if(attackButtons && c.b==2)return "ATTACK_BUTTON_RESERVED";
      tapButtons=c.b;sink.mouse(attackButtons|tapButtons,0,0);
    }
    else {
      uint8_t report[8]={0};int slot=2;
      int keys[]={c.b,c.c};for(int key:keys){if(key>=224)report[0]|=(1<<(key-224));else if(key)report[slot++]=key;}
      sink.keyboard(report);
    }
    held=true;began=now;duration=c.a;return nullptr;
  }
};

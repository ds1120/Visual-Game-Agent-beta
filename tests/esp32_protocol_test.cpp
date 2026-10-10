#include <assert.h>
#include <string>
#include "../firmware/esp32_ble_hid/protocol.h"
struct Sink: HIDSink {
  int key=0,buttons=0,x=0,y=0;
  void keyboard(const uint8_t* b) override {key=b[2];}
  void mouse(uint8_t b,int8_t dx,int8_t dy) override {buttons=b;x=dx;y=dy;}
};
bool parse(const char* text,Command& c){std::string s(text);return parseCommand(&s[0],c);}
int main(){
  Command c;Sink sink;HIDState state;
  char key[]="VGA_BLE_1 1 KEY 40 20 0";assert(parseCommand(key,c));
  assert(state.execute(c,100,true,sink)==nullptr);assert(sink.key==20);
  state.tick(139,true,sink);assert(sink.key==20);
  state.tick(140,true,sink);assert(sink.key==0 && !state.held);
  assert(state.execute(c,200,false,sink)!=nullptr);assert(!state.held);
  assert(state.execute(c,200,true,sink)==nullptr);
  state.tick(210,false,sink);assert(!state.held && sink.key==0);
  char click[]="VGA_BLE_1 2 CLICK 150 1";assert(parseCommand(click,c));
  assert(!state.execute(c,300,true,sink));assert(sink.buttons==1);
  char stop[]="VGA_BLE_1 3 STOP";assert(parseCommand(stop,c));
  assert(!state.execute(c,301,true,sink));assert(!sink.buttons && !state.held);
  for(const char* text:{"VGA_BLE_1 4 KEY 500 20 0","VGA_BLE_1 4 MOVE 128 0","VGA_BLE_1 4 CLICK 40 7","VGA_BLE_1 4 STOP 9","VGA_BLE_1 4 KEY 40 0 0","garbage"}){Command bad;assert(!parse(text,bad));}
  char wrap[]="VGA_BLE_1 5 KEY 40 4 0";assert(parseCommand(wrap,c));
  assert(!state.execute(c,0xfffffff0U,true,sink));state.tick(0x20U,true,sink);assert(!state.held);
  assert(parse("VGA_BLE_1 6 HOLD 2 1000",c));assert(!state.execute(c,1000,true,sink));assert(sink.buttons==2);
  state.tick(1400,true,sink);assert(sink.buttons==2);
  assert(parse("VGA_BLE_1 7 HOLD 2 1000",c));assert(!state.execute(c,1400,true,sink));
  assert(parse("VGA_BLE_1 8 KEY 40 20 0",c));assert(!state.execute(c,1410,true,sink));assert(sink.buttons==2 && sink.key==20);
  state.tick(1450,true,sink);assert(sink.key==0 && sink.buttons==2);
  assert(parse("VGA_BLE_1 9 MOVE 20 0",c));assert(!state.execute(c,1500,true,sink));assert(sink.buttons==2 && sink.x==20);
  assert(parse("VGA_BLE_1 10 CLICK 40 1",c));assert(!state.execute(c,1510,true,sink));assert(sink.buttons==3);
  assert(parse("VGA_BLE_1 11 END_TAP",c));assert(!state.execute(c,1511,true,sink));assert(sink.buttons==2);
  state.tick(2400,true,sink);assert(sink.buttons==0);
  assert(parse("VGA_BLE_1 12 HOLD 2 1000",c));assert(!state.execute(c,2500,true,sink));
  assert(parse("VGA_BLE_1 13 RELEASE",c));assert(!state.execute(c,2510,true,sink));assert(sink.buttons==0);
  assert(parse("VGA_BLE_1 14 HOLD 2 1000",c));assert(!state.execute(c,2600,true,sink));state.tick(2601,false,sink);assert(sink.buttons==0);
  assert(parse("VGA_BLE_1 15 HOLD 1 350",c));
  assert(!state.execute(c,3000,true,sink));assert(sink.buttons==1);
  assert(parse("VGA_BLE_1 16 MOVE 20 -30",c));
  assert(!state.execute(c,3010,true,sink));assert(sink.buttons==1 && sink.x==20 && sink.y==-30);
  state.tick(3349,true,sink);assert(sink.buttons==1);
  state.tick(3350,true,sink);assert(sink.buttons==0);
  assert(parse("VGA_BLE_1 17 HOLD 1 350",c));assert(!state.execute(c,3400,true,sink));
  assert(parse("VGA_BLE_1 18 STOP",c));assert(!state.execute(c,3401,true,sink));assert(sink.buttons==0);
  assert(!parse("VGA_BLE_1 19 HOLD 3 350",c));assert(!parse("VGA_BLE_1 15 HOLD 2 2000",c));
}

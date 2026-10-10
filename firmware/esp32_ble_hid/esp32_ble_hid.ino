// Arduino ESP32 core 2.0.17. Composite BLE keyboard + relative mouse.
// USB is a serial command channel, not USB HID.
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEHIDDevice.h>
#include <BLE2902.h>
#include <BLESecurity.h>
#include "protocol.h"

BLEHIDDevice* hid;
BLECharacteristic *keyboardReport,*mouseReport;
volatile bool connected=false,restartAdvertising=false;
uint8_t reportMap[]={
  // Keyboard report ID 1, 8-byte standard boot-style input.
  0x05,0x01,0x09,0x06,0xA1,0x01,0x85,0x01,
  0x05,0x07,0x19,0xE0,0x29,0xE7,0x15,0x00,0x25,0x01,
  0x75,0x01,0x95,0x08,0x81,0x02,0x95,0x01,0x75,0x08,0x81,0x01,
  0x95,0x06,0x75,0x08,0x15,0x00,0x25,0x65,0x05,0x07,
  0x19,0x00,0x29,0x65,0x81,0x00,0xC0,
  // Mouse report ID 2: buttons, signed relative X/Y, wheel.
  0x05,0x01,0x09,0x02,0xA1,0x01,0x85,0x02,0x09,0x01,0xA1,0x00,
  0x05,0x09,0x19,0x01,0x29,0x03,0x15,0x00,0x25,0x01,
  0x95,0x03,0x75,0x01,0x81,0x02,0x95,0x01,0x75,0x05,0x81,0x01,
  0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,
  0x15,0x81,0x25,0x7F,0x75,0x08,0x95,0x03,0x81,0x06,0xC0,0xC0
};
class Callbacks:public BLEServerCallbacks {
  void onConnect(BLEServer*) override {connected=true;}
  void onDisconnect(BLEServer*) override {connected=false;restartAdvertising=true;}
};
bool ready(){
  if(!connected)return false;
  auto k=(BLE2902*)keyboardReport->getDescriptorByUUID(BLEUUID((uint16_t)0x2902));
  auto m=(BLE2902*)mouseReport->getDescriptorByUUID(BLEUUID((uint16_t)0x2902));
  return k && m && k->getNotifications() && m->getNotifications();
}
class Sink:public HIDSink {
  void keyboard(const uint8_t* b) override {keyboardReport->setValue((uint8_t*)b,8);if(connected)keyboardReport->notify();}
  void mouse(uint8_t buttons,int8_t x,int8_t y) override {uint8_t b[]={buttons,(uint8_t)x,(uint8_t)y,0};mouseReport->setValue(b,4);if(connected)mouseReport->notify();}
} sink;
HIDState state;
char line[128];size_t length=0;bool overflow=false;
uint32_t previousId=0;const char* previousError=nullptr;
void reply(uint32_t id,const char* error){
  Serial.printf("{\"protocol\":\"VGA_BLE_1\",\"id\":%lu,\"ok\":%s,\"ready\":%s,\"connected\":%s,\"firmware\":\"1.2\",\"hold_attack\":true,\"hold_move\":true,\"name\":\"VisualAgent-ESP32\",\"error\":\"%s\"}\n",(unsigned long)id,error?"false":"true",ready()?"true":"false",connected?"true":"false",error?error:"");
}
void handleLine(){
  Command c;
  if(!parseCommand(line,c)){state.release(sink);reply(c.id,"INVALID_COMMAND");return;}
  // Never execute an input twice when a duplicate serial packet arrives.
  if(c.id==previousId){reply(c.id,previousError);return;}
  const char* error=state.execute(c,millis(),ready(),sink);
  previousId=c.id;previousError=error;reply(c.id,error);
}
void setup(){
  Serial.begin(115200);
  BLEDevice::init("VisualAgent-ESP32");
  auto server=BLEDevice::createServer();server->setCallbacks(new Callbacks());
  hid=new BLEHIDDevice(server);keyboardReport=hid->inputReport(1);mouseReport=hid->inputReport(2);
  hid->manufacturer()->setValue("Visual Agent");hid->pnp(0x02,0x303A,0x4001,0x0100);
  hid->hidInfo(0x00,0x01);hid->reportMap(reportMap,sizeof(reportMap));hid->startServices();hid->setBatteryLevel(100);
  auto security=new BLESecurity();security->setAuthenticationMode(ESP_LE_AUTH_BOND);
  security->setCapability(ESP_IO_CAP_NONE);security->setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK|ESP_BLE_ID_KEY_MASK);
  auto advertising=server->getAdvertising();advertising->setAppearance(0x03C0);
  advertising->addServiceUUID(hid->hidService()->getUUID());advertising->setScanResponse(true);advertising->start();
  state.release(sink);
}
void loop(){
  state.tick(millis(),ready(),sink);
  if(restartAdvertising){restartAdvertising=false;BLEDevice::startAdvertising();}
  // Bound work per loop so serial floods cannot starve key release timers.
  for(int n=0;n<128 && Serial.available();n++){
    char c=(char)Serial.read();if(c=='\r')continue;
    if(c=='\n'){
      if(overflow){state.release(sink);reply(0,"LINE_TOO_LONG");}
      else{line[length]=0;handleLine();}
      length=0;overflow=false;
    }else if(length<sizeof(line)-1)line[length++]=c;else overflow=true;
  }
  delay(1);
}

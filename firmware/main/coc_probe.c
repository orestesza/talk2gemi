#include "coc_probe.h"
#include "host/ble_hs.h"
#include "host/ble_l2cap.h"
#include "nimble/nimble_port.h"
#include "os/os_mbuf.h"
#include "os/os_mempool.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <string.h>
#define FRAME 514
#define COUNT 512
#define WINDOW 4
#define BUFFERS 6
static os_membuf_t storage[OS_MEMPOOL_SIZE(BUFFERS,600)];
static struct os_mempool mem;
static struct os_mbuf_pool pool;
static struct ble_l2cap_chan *channel;
static struct ble_npl_event connect_event,pump_event;
static struct ble_npl_callout watchdog;
static uint16_t pending_conn,pending_psm;
static unsigned sent,received,max_inflight,stalls;
static bool running,failed,stalled;
static int64_t began,last_progress;
static uint8_t packet[FRAME],incoming[FRAME];
static size_t used;
static void pump(struct ble_npl_event *event);
static void schedule(void){
 if(!ble_npl_event_is_queued(&pump_event))ble_npl_eventq_put(nimble_port_get_dflt_eventq(),&pump_event);
}
static void finish(void){
 running=false;ble_npl_callout_stop(&watchdog);
 ESP_LOGI("coc_probe","result=%s sent=%u echoed=%u elapsed_us=%lld window=%u stalls=%u heap=%u stack_free=%u",(!failed&&received==COUNT)?"PASS":"FAIL",sent,received,esp_timer_get_time()-began,max_inflight,stalls,(unsigned)esp_get_free_heap_size(),(unsigned)uxTaskGetStackHighWaterMark(NULL));
 if(failed&&channel)ble_l2cap_disconnect(channel);
}
// All channel dereferences and sends run on NimBLE's host event queue. The
// disconnect callback runs there too, so no FreeRTOS task retains a stale chan.
static void pump(struct ble_npl_event *event){
 (void)event;if(!running)return;
 int64_t now=esp_timer_get_time();
 if(!channel||now-last_progress>3000000||now-began>60000000)failed=true;
 if(failed||received==COUNT){finish();return;}
 while(sent<COUNT&&sent-received<WINDOW&&!stalled){
  struct os_mbuf *sdu=os_mbuf_get_pkthdr(&pool,0);if(!sdu)break;
  packet[0]=0;packet[1]=2;packet[2]=sent;packet[3]=sent>>8;
  for(size_t j=4;j<FRAME;j++)packet[j]=(uint8_t)(sent+j);
  if(os_mbuf_append(sdu,packet,FRAME)){os_mbuf_free_chain(sdu);failed=true;break;}
  int rc=ble_l2cap_send(channel,sdu);
  if(rc==BLE_HS_EBUSY||rc==BLE_HS_EBADDATA){
   // These two paths reject before taking SDU ownership in this pinned SDK.
   os_mbuf_free_chain(sdu);if(rc==BLE_HS_EBADDATA)failed=true;break;
  }
  // v5.5.3 consumes/frees SDU on other errors too (including ENOMEM).
  // ESTALLED keeps it internally until credits arrive: never resubmit/free it.
  if(rc&&rc!=BLE_HS_ESTALLED){failed=true;break;}
  sent++;if(sent-received>max_inflight)max_inflight=sent-received;
  if(rc==BLE_HS_ESTALLED){stalled=true;stalls++;}
 }
 if(failed){finish();return;}
 ble_npl_time_t ticks=ble_npl_time_ms_to_ticks32(10);
 ble_npl_callout_reset(&watchdog,ticks?ticks:1);
}
static int event_cb(struct ble_l2cap_event *event,void *arg){
 (void)arg;
 switch(event->type){
 case BLE_L2CAP_EVENT_COC_CONNECTED:{
  if(event->connect.status){ESP_LOGW("coc_probe","connect failed=%d",event->connect.status);break;}
  channel=event->connect.chan;failed=false;stalled=false;used=0;sent=received=max_inflight=stalls=0;
  running=true;began=last_progress=esp_timer_get_time();
  struct ble_gap_conn_desc desc;
  int interval=!ble_gap_conn_find(event->connect.conn_handle,&desc)?desc.conn_itvl:0;
  ESP_LOGI("coc_probe","begin pipeline: frames=%u payload=512 window=%u interval_units=%d heap=%u",COUNT,WINDOW,interval,(unsigned)esp_get_free_heap_size());
  schedule();break;}
 case BLE_L2CAP_EVENT_COC_DISCONNECTED:
  channel=NULL;if(running){failed=true;finish();}break;
 case BLE_L2CAP_EVENT_COC_DATA_RECEIVED:{
  struct os_mbuf *sdu=event->receive.sdu_rx;size_t n=OS_MBUF_PKTLEN(sdu);
  for(size_t i=0;i<n;i++){
   os_mbuf_copydata(sdu,i,1,incoming+used);used++;
   if(used==FRAME){
    unsigned seq=incoming[2]|incoming[3]<<8;
    if(!running||received>=COUNT||seq!=received||incoming[0]!=0||incoming[1]!=2)failed=true;
    for(size_t j=4;j<FRAME;j++)if(incoming[j]!=(uint8_t)(seq+j))failed=true;
    used=0;received++;last_progress=esp_timer_get_time();
   }
  }
  os_mbuf_free_chain(sdu);
  struct os_mbuf *next=os_mbuf_get_pkthdr(&pool,0);
  if(!next){failed=true;schedule();break;}
  if(ble_l2cap_recv_ready(event->receive.chan,next)){os_mbuf_free_chain(next);failed=true;}
  schedule();break;}
 case BLE_L2CAP_EVENT_COC_TX_UNSTALLED:
  stalled=false;if(event->tx_unstalled.status)failed=true;schedule();break;
 default:break;
 }
 return 0;
}
static void connect_cb(struct ble_npl_event *event){
 (void)event;if(channel||running||!pending_psm)return;
 struct ble_gap_conn_desc desc;
 if(ble_gap_conn_find(pending_conn,&desc)||!desc.sec_state.encrypted||!desc.sec_state.authenticated)return;
 struct os_mbuf *sdu=os_mbuf_get_pkthdr(&pool,0);if(!sdu)return;
 int rc=ble_l2cap_connect(pending_conn,pending_psm,FRAME,sdu,event_cb,NULL);
 if(rc){os_mbuf_free_chain(sdu);ESP_LOGW("coc_probe","request failed=%d",rc);}
}
void coc_probe_init(void){
 assert(os_mempool_init(&mem,BUFFERS,600,storage,"coc_probe")==0);
 assert(os_mbuf_pool_init(&pool,&mem,600,BUFFERS)==0);
 ble_npl_event_init(&pump_event,pump,NULL);ble_npl_event_init(&connect_event,connect_cb,NULL);
 assert(ble_npl_callout_init(&watchdog,nimble_port_get_dflt_eventq(),pump,NULL)==0);
}
void coc_probe_connect(uint16_t conn,uint16_t psm){
 if(!psm)return;
 pending_conn=conn;pending_psm=psm;
 ble_npl_eventq_put(nimble_port_get_dflt_eventq(),&connect_event);
}

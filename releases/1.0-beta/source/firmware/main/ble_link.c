#include "ble_link.h"
#include "opus.h"
#include "app.h"
#include "audio_stream.h"
#include "ima_codec.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_sm.h"
#include "host/ble_store.h"
#include "host/util/util.h"
#include "store/config/ble_store_config.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>
#define MAX_CANDIDATES 8
#define MAX_FRAME 768
static const ble_uuid128_t svc=BLE_UUID128_INIT(0x30,0x44,0x3d,0xcf,0xcc,0x74,0x29,0x8f,0x31,0x4b,0x4b,0x3b,0x01,0x00,0x4f,0x83);
static uint16_t conn=BLE_HS_CONN_HANDLE_NONE,svc_start,svc_end,identity_handle,up_handle,down_handle,cccd;
static uint8_t own_addr_type;
static uint32_t opus_state[4608];
static OpusDecoder *opus_decoder;
static bool opus_available;
static bool synced,ready,hello_pending,auto_reconnect;
static int comparison_code=-1;
static ble_addr_t addresses[MAX_CANDIDATES];
static ble_candidate_t candidates[MAX_CANDIDATES];
static int candidate_count;
static int reconnect_index;
static uint16_t seq,fragment_id,ack_seq,expected_seq;
static uint32_t turn;
static bool have_sequence;
static SemaphoreHandle_t tx_lock,wire_lock,write_done,frame_ack;
static QueueHandle_t rx_queue;
static uint8_t partial[MAX_FRAME];
static size_t partial_len,partial_total;
static uint16_t partial_id;
void ble_store_config_init(void);
static int gap_event(struct ble_gap_event *ev,void *arg);
static uint16_t le16(const uint8_t *p){return p[0]|p[1]<<8;}
static void put16(uint8_t *p,uint16_t n){p[0]=n;p[1]=n>>8;}
static void terminate(void){if(conn!=BLE_HS_CONN_HANDLE_NONE)ble_gap_terminate(conn,BLE_ERR_REM_USER_CONN_TERM);}
static bool send_frame(int kind,const uint8_t *payload,size_t length,uint16_t ack_number){
    int64_t started=esp_timer_get_time();
    if(conn==BLE_HS_CONN_HANDLE_NONE||!up_handle||length+10>MAX_FRAME)return false;
    /* Kind 4 is streaming ADPCM: no application receipt wait.
     * Control frames keep one outstanding receipt. Receipt ACKs must bypass that
     * wait, or crossed device audio and Mac state frames deadlock. wire_lock
     * still prevents fragments from different frames from interleaving. */
    if(kind==3){
        if(xSemaphoreTake(wire_lock,pdMS_TO_TICKS(4000))!=pdTRUE)return false;
        uint8_t receipt[16];put16(receipt,++fragment_id);put16(receipt+2,0);put16(receipt+4,10);
        receipt[6]=1;receipt[7]=3;memcpy(receipt+8,&turn,4);put16(receipt+12,ack_number);put16(receipt+14,0);
        int rc=BLE_HS_ENOMEM;
        for(int attempt=0;attempt<100;attempt++){
            rc=ble_gattc_write_no_rsp_flat(conn,up_handle,receipt,sizeof(receipt));
            if(!rc||(rc!=BLE_HS_ENOMEM&&rc!=BLE_HS_EBUSY))break;
            vTaskDelay(pdMS_TO_TICKS(3));
        }
        xSemaphoreGive(wire_lock);
        if(rc)terminate();
        return rc==0;
    }
    if(xSemaphoreTake(tx_lock,pdMS_TO_TICKS(4000))!=pdTRUE)return false;
    if(xSemaphoreTake(wire_lock,pdMS_TO_TICKS(4000))!=pdTRUE){xSemaphoreGive(tx_lock);return false;}
    static uint8_t frame[MAX_FRAME],chunk[512];
    uint16_t number=kind==3?ack_number:++seq;
    frame[0]=1;frame[1]=kind;memcpy(frame+2,&turn,4);put16(frame+6,number);put16(frame+8,length);
    if(length)memcpy(frame+10,payload,length);
    size_t total=length+10,offset=0,mtu=ble_att_mtu(conn);
    size_t max_payload=mtu>9?mtu-9:1;if(max_payload>sizeof(chunk)-6)max_payload=sizeof(chunk)-6;
    uint16_t id=++fragment_id;
    bool ok=true;
    xSemaphoreTake(frame_ack,0);ack_seq=number;
    while(offset<total){
        size_t n=total-offset;if(n>max_payload)n=max_payload;
        put16(chunk,id);put16(chunk+2,offset);put16(chunk+4,total);memcpy(chunk+6,frame+offset,n);
        int rc=BLE_HS_ENOMEM;
        for(int attempt=0;attempt<100;attempt++){
            rc=ble_gattc_write_no_rsp_flat(conn,up_handle,chunk,n+6);
            if(!rc)break;
            if(rc!=BLE_HS_ENOMEM&&rc!=BLE_HS_EBUSY)break;
            vTaskDelay(pdMS_TO_TICKS(3));
        }
        if(rc){ok=false;break;}
        offset+=n;
    }
    xSemaphoreGive(wire_lock);
    if(ok&&kind!=4)ok=xSemaphoreTake(frame_ack,pdMS_TO_TICKS(3000))==pdTRUE;
    xSemaphoreGive(tx_lock);
#if CONFIG_TALK2GEMI_USB_DIAGNOSTICS
    if(kind==2){static int count;static int64_t sum,maximum;int64_t elapsed=esp_timer_get_time()-started;sum+=elapsed;if(elapsed>maximum)maximum=elapsed;if(++count%32==0)ESP_LOGI("BLE","Uplink frames=%d mean=%lldus max=%lldus",count,sum/count,maximum);}
#else
    (void)started;
#endif
    if(!ok) { terminate(); }
    return ok;
}
bool ble_link_send_json(const char *json){
    if(strstr(json,"\"type\":\"start\""))turn++;
    return send_frame(1,(const uint8_t*)json,strlen(json),0);
}
bool ble_link_send_audio(const void *pcm,size_t length){
    uint8_t encoded[518];
    if(length>1024||length%2)return false;
    size_t size=ima_encode(pcm,length/2,encoded,sizeof(encoded));
    return size&&send_frame(4,encoded,size,0);
}
static int subscribed(uint16_t h,const struct ble_gatt_error *error,struct ble_gatt_attr *attr,void *arg){
    (void)h;(void)attr;(void)arg;
    ESP_LOGI("BLE","Subscription status=%d",error->status);
    if(error->status){terminate();return 0;}
    hello_pending=true;return 0;
}
static void subscribe(void){uint8_t value[2]={1,0};if(!cccd||ble_gattc_write_flat(conn,cccd,value,2,subscribed,NULL))terminate();}
static int identity_read(uint16_t h,const struct ble_gatt_error *error,struct ble_gatt_attr *attr,void *arg){
    (void)h;(void)arg;
    ESP_LOGI("BLE","Mac identity read status=%d",error->status);
    if(error->status||!attr){terminate();return 0;}
    char value[256]={0};int length=OS_MBUF_PKTLEN(attr->om);if(length>=sizeof(value)){terminate();return 0;}
    os_mbuf_copydata(attr->om,0,length,value);
    cJSON *root=cJSON_Parse(value),*host=cJSON_GetObjectItem(root,"host_id");
    bool matches=cJSON_IsString(host)&&(!app_settings.host_id[0]||strcmp(app_settings.host_id,host->valuestring)==0);
    cJSON_Delete(root);
    if(!matches){app_set_state(UI_STATE_ERROR,"Different Mac: use Change Mac");terminate();return 0;}
    auto_reconnect=false;
    struct ble_gap_conn_desc d;
    if(!ble_gap_conn_find(conn,&d)&&d.sec_state.encrypted)subscribe();
    else {
        /* Old development builds used unauthenticated Just Works bonds. They
         * cannot satisfy the Mac's trusted notification permission. */
        struct ble_store_key_sec key={0};struct ble_store_value_sec saved;
        if(!ble_gap_conn_find(conn,&d)){
            key.peer_addr=d.peer_id_addr;
            if(!ble_store_read_peer_sec(&key,&saved)&&!saved.authenticated)
                ble_store_util_delete_peer(&d.peer_id_addr);
        }
        int rc=ble_gap_security_initiate(conn);
        ESP_LOGI("BLE","Encryption initiation status=%d",rc);
        if(rc)terminate();
    }
    return 0;
}
static int descriptor(uint16_t h,const struct ble_gatt_error *error,uint16_t chr,const struct ble_gatt_dsc *dsc,void *arg){
    (void)h;(void)chr;(void)arg;
    if(!error->status&&ble_uuid_u16(&dsc->uuid.u)==BLE_GATT_DSC_CLT_CFG_UUID16)cccd=dsc->handle;
    else if(error->status==BLE_HS_EDONE){if(ble_gattc_read(conn,identity_handle,identity_read,NULL))terminate();}
    else if(error->status) { terminate(); }
    return 0;
}
static int characteristic(uint16_t h,const struct ble_gatt_error *error,const struct ble_gatt_chr *chr,void *arg){
    (void)h;(void)arg;
    if(!error->status){
        if(chr->uuid.u.type==BLE_UUID_TYPE_128){
            const uint8_t *u=chr->uuid.u128.value;
            if(u[12]==2)identity_handle=chr->val_handle;
            else if(u[12]==3)up_handle=chr->val_handle;
            else if(u[12]==4)down_handle=chr->val_handle;
        }
    }else if(error->status==BLE_HS_EDONE){
        if(!identity_handle||!up_handle||!down_handle||ble_gattc_disc_all_dscs(conn,down_handle,svc_end,descriptor,NULL))terminate();
    }else terminate();return 0;
}
static int service(uint16_t h,const struct ble_gatt_error *error,const struct ble_gatt_svc *s,void *arg){
    (void)h;(void)arg;
    if(!error->status){svc_start=s->start_handle;svc_end=s->end_handle;}
    else if(error->status==BLE_HS_EDONE){
        if(!svc_start||ble_gattc_disc_all_chrs(conn,svc_start,svc_end,characteristic,NULL))terminate();
    }else terminate();return 0;
}
static int mtu_done(uint16_t h,const struct ble_gatt_error *error,uint16_t mtu,void *arg){
    (void)h;(void)error;(void)mtu;(void)arg;
    if(ble_gattc_disc_svc_by_uuid(conn,&svc.u,service,NULL)) { terminate(); }
    return 0;
}
static void notification(const struct os_mbuf *om){
    uint8_t data[512];int n=OS_MBUF_PKTLEN(om);if(n<6||n>sizeof(data)) {terminate();return;}
    os_mbuf_copydata(om,0,n,data);
    uint16_t id=le16(data),offset=le16(data+2),total=le16(data+4);
    if(offset==0){partial_id=id;partial_len=0;partial_total=total;}
    if(id!=partial_id||total!=partial_total||offset!=partial_len||total>MAX_FRAME||partial_len+n-6>total){terminate();return;}
    memcpy(partial+partial_len,data+6,n-6);partial_len+=n-6;
    if(partial_len!=total)return;
    if(total<10||partial[0]!=1||le16(partial+8)!=total-10){terminate();return;}
    if(partial[1]==3){if(le16(partial+6)==ack_seq)xSemaphoreGive(frame_ack);return;}
    uint16_t number=le16(partial+6);
    if(have_sequence&&number!=expected_seq){terminate();return;}
    have_sequence=true;expected_seq=number+1;
    uint8_t packet[MAX_FRAME]={0};memcpy(packet,partial,total);
    if(xQueueSend(rx_queue,packet,0)!=pdTRUE){app_set_state(UI_STATE_ERROR,"BLE receive buffer full");terminate();}
}
static int gap_event(struct ble_gap_event *ev,void *arg){
    (void)arg;
    switch(ev->type){
    case BLE_GAP_EVENT_DISC: {
        struct ble_hs_adv_fields fields;
        if(ble_hs_adv_parse_fields(&fields,ev->disc.data,ev->disc.length_data))break;
        bool found=false;for(int i=0;i<fields.num_uuids128;i++)if(!ble_uuid_cmp(&fields.uuids128[i].u,&svc.u))found=true;
        int i;for(i=0;i<candidate_count;i++)if(!memcmp(&addresses[i],&ev->disc.addr,sizeof(ble_addr_t)))break;
        if(!found&&i==candidate_count)break;
        if(i==candidate_count){if(candidate_count==MAX_CANDIDATES)break;addresses[i]=ev->disc.addr;candidate_count++;}
        candidates[i].rssi=ev->disc.rssi;
        if(fields.name_len){int len=fields.name_len;if(len>=sizeof(candidates[i].name))len=sizeof(candidates[i].name)-1;memcpy(candidates[i].name,fields.name,len);candidates[i].name[len]=0;}
        else snprintf(candidates[i].name,sizeof(candidates[i].name),"Mac %02X%02X%02X",ev->disc.addr.val[2],ev->disc.addr.val[1],ev->disc.addr.val[0]);
        break;}
    case BLE_GAP_EVENT_DISC_COMPLETE:
        if(auto_reconnect&&candidate_count){reconnect_index=0;ble_link_connect(0);}break;
    case BLE_GAP_EVENT_CONNECT:
        if(ev->connect.status){app_set_state(UI_STATE_ERROR,"Bluetooth connection failed");break;}
        conn=ev->connect.conn_handle;ready=false;have_sequence=false;seq=0;turn=0;
        svc_start=identity_handle=up_handle=down_handle=cccd=0;
        if(ble_gattc_exchange_mtu(conn,mtu_done,NULL)) { terminate(); }
        break;
    case BLE_GAP_EVENT_ENC_CHANGE:
        ESP_LOGI("BLE","Encryption change status=%d",ev->enc_change.status);
        {struct ble_gap_conn_desc d;if(!ble_gap_conn_find(conn,&d))ESP_LOGI("BLE","encrypted=%d authenticated=%d bonded=%d",d.sec_state.encrypted,d.sec_state.authenticated,d.sec_state.bonded);}
        if(ev->enc_change.status)terminate();else subscribe();break;
    case BLE_GAP_EVENT_PASSKEY_ACTION: {
        ESP_LOGI("BLE","Passkey action=%d",ev->passkey.params.action);
        if(ev->passkey.params.action==BLE_SM_IOACT_NUMCMP){
            comparison_code=ev->passkey.params.numcmp;
            char text[100];snprintf(text,sizeof(text),"{\"type\":\"compare_pair_code\",\"code\":\"%06d\"}",comparison_code);
            app_receive_json(text,strlen(text),2);break;
        }
        if(ev->passkey.params.action!=BLE_SM_IOACT_DISP){terminate();break;}
        struct ble_sm_io io={.action=BLE_SM_IOACT_DISP,.passkey=esp_random()%1000000};
        char text[100];snprintf(text,sizeof(text),"{\"type\":\"system_pair_code\",\"code\":\"%06lu\"}",(unsigned long)io.passkey);
        app_receive_json(text,strlen(text),2);
        if(ble_sm_inject_io(ev->passkey.conn_handle,&io))terminate();
        break;}
    case BLE_GAP_EVENT_REPEAT_PAIRING: {
        struct ble_gap_conn_desc d;if(!ble_gap_conn_find(conn,&d))ble_store_util_delete_peer(&d.peer_id_addr);
        return BLE_GAP_REPEAT_PAIRING_RETRY;}
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI("BLE","Disconnected reason=%d",ev->disconnect.reason);
        comparison_code=-1;
        conn=BLE_HS_CONN_HANDLE_NONE;ready=false;hello_pending=false;app_link_status(2,false);
        xSemaphoreGive(write_done);xSemaphoreGive(frame_ack);
        if(auto_reconnect&&++reconnect_index<candidate_count)ble_link_connect(reconnect_index);
        break;
    case BLE_GAP_EVENT_NOTIFY_RX:notification(ev->notify_rx.om);break;
    default:break;
    }return 0;
}
static void sync_cb(void){if(!ble_hs_id_infer_auto(0,&own_addr_type))synced=true;}
static void host_task(void *arg){(void)arg;nimble_port_run();nimble_port_freertos_deinit();}
static void receive_task(void *arg){
    (void)arg;uint8_t packet[MAX_FRAME+1];static int16_t pcm[960];
    while(1){
        if(hello_pending){
            hello_pending=false;
            char hello[256];snprintf(hello,sizeof(hello),"{\"type\":\"hello\",\"device_id\":\"%s\",\"token\":\"%s\",\"downlink_opus\":%s}",app_device_id(),app_settings.token,opus_available?"true":"false");
            ble_link_send_json(hello);
        }
        if(xQueueReceive(rx_queue,packet,pdMS_TO_TICKS(50))!=pdTRUE)continue;
        size_t n=le16(packet+8);
        if(packet[1]==1){
            char *text=(char*)packet+10;text[n]=0;
            app_receive_json(text,n,2);
            cJSON *r=cJSON_Parse(text),*t=cJSON_GetObjectItem(r,"type");
            if(cJSON_IsString(t)&&!strcmp(t->valuestring,"hello")){ready=true;app_link_status(2,true);}
            cJSON *v=cJSON_GetObjectItem(r,"value");
            if(opus_available&&cJSON_IsString(t)&&(!strcmp(t->valuestring,"audio_abort")||(!strcmp(t->valuestring,"state")&&cJSON_IsString(v)&&!strcmp(v->valuestring,"speaking"))))
                opus_decoder_ctl(opus_decoder,OPUS_RESET_STATE);
            cJSON_Delete(r);
        }else if(packet[1]==5){
            if(!opus_available||n<1||n>512){terminate();continue;}
            int64_t began=esp_timer_get_time();
            int samples=opus_decode(opus_decoder,packet+10,(opus_int32)n,pcm,960,0);
            int64_t elapsed=esp_timer_get_time()-began;
            static unsigned count;static int64_t maximum;
            if(elapsed>maximum)maximum=elapsed;
            if(++count==1||count%32==0)ESP_LOGI("opus","frames=%u decode_max_us=%lld stack_free=%u heap=%u",count,maximum,(unsigned)uxTaskGetStackHighWaterMark(NULL),(unsigned)esp_get_free_heap_size());
            if(samples<=0||audio_stream_play_chunk(pcm,(size_t)samples*2)!=ESP_OK){terminate();continue;}
        }else if(packet[1]==2){
            size_t samples=ima_decode(packet+10,n,pcm,512);
            if(!samples||audio_stream_play_chunk(pcm,samples*2)!=ESP_OK){terminate();continue;}
        }
        send_frame(3,NULL,0,le16(packet+6));
    }
}
esp_err_t ble_link_init(void){
    tx_lock=xSemaphoreCreateMutex();wire_lock=xSemaphoreCreateMutex();write_done=xSemaphoreCreateBinary();frame_ack=xSemaphoreCreateBinary();
    rx_queue=xQueueCreate(3,MAX_FRAME);
    if(!tx_lock||!wire_lock||!write_done||!frame_ack||!rx_queue)return ESP_ERR_NO_MEM;
    ESP_RETURN_ON_ERROR(nimble_port_init(),"BLE","NimBLE init");
    int size=opus_decoder_get_size(1);
    opus_decoder=(OpusDecoder*)opus_state;
    opus_available=size>0&&size<=sizeof(opus_state)&&opus_decoder_init(opus_decoder,16000,1)==OPUS_OK;
    ESP_LOGI("opus","decoder=%d bytes=%d reserved=%u",opus_available,size,(unsigned)sizeof(opus_state));
    /* macOS discovers the peer's standard GATT services even when the ESP is
     * the GAP central. Without a GATT server the read is silently unanswered,
     * and macOS drops the whole connection after its 30-second ATT timeout. */
    ble_svc_gap_init();ble_svc_gatt_init();
    ble_svc_gap_device_name_set("talk2gemi");
    ble_hs_cfg.sync_cb=sync_cb;ble_hs_cfg.store_status_cb=ble_store_util_status_rr;
    ble_hs_cfg.sm_bonding=1;ble_hs_cfg.sm_sc=1;ble_hs_cfg.sm_mitm=1;ble_hs_cfg.sm_io_cap=BLE_HS_IO_DISPLAY_YESNO;
    ble_hs_cfg.sm_our_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist=BLE_SM_PAIR_KEY_DIST_ENC|BLE_SM_PAIR_KEY_DIST_ID;
    ble_store_config_init();ble_att_set_preferred_mtu(247);
    nimble_port_freertos_init(host_task);
    if(xTaskCreate(receive_task,"ble_audio",12288,NULL,4,NULL)!=pdPASS)return ESP_ERR_NO_MEM;
    return ESP_OK;
}
void ble_link_scan(void){
    if(!synced||conn!=BLE_HS_CONN_HANDLE_NONE)return;
    ble_gap_disc_cancel();candidate_count=0;
    struct ble_gap_disc_params p={.passive=0,.itvl=160,.window=80,.filter_duplicates=1};
    ble_gap_disc(own_addr_type,5000,&p,gap_event,NULL);
}
int ble_link_candidates(ble_candidate_t *out,int count){int n=candidate_count;if(n>count)n=count;memcpy(out,candidates,n*sizeof(*out));return n;}
void ble_link_connect(int i){
    if(!synced||i<0||i>=candidate_count)return;
    ble_gap_disc_cancel();
    if(conn!=BLE_HS_CONN_HANDLE_NONE){terminate();return;}
    struct ble_gap_conn_params p={.scan_itvl=16,.scan_window=16,.itvl_min=6,.itvl_max=12,.latency=0,.supervision_timeout=400,.min_ce_len=0,.max_ce_len=0};
    ble_gap_connect(own_addr_type,&addresses[i],10000,&p,gap_event,NULL);
}
void ble_link_reconnect(void){if(!app_settings.token[0])return;auto_reconnect=true;ble_link_scan();}
void ble_link_forget(void){if(ready)ble_link_send_json("{\"type\":\"forget\"}");terminate();ble_store_clear();}
bool ble_link_connected(void){return ready;}
int ble_link_rssi(void){int8_t rssi;if(conn==BLE_HS_CONN_HANDLE_NONE||ble_gap_conn_rssi(conn,&rssi))return 0;return rssi;}
void ble_link_stop(void){ble_gap_disc_cancel();terminate();nimble_port_stop();}
void ble_link_disconnect(void){auto_reconnect=false;terminate();}
bool ble_link_confirm_pairing(int code){
    if(comparison_code<0||(code>=0&&code!=comparison_code))return false;
    struct ble_sm_io io={.action=BLE_SM_IOACT_NUMCMP,.numcmp_accept=1};
    comparison_code=-1;return ble_sm_inject_io(conn,&io)==0;
}
void ble_link_reject_pairing(void){
    if(comparison_code>=0){struct ble_sm_io io={.action=BLE_SM_IOACT_NUMCMP,.numcmp_accept=0};comparison_code=-1;ble_sm_inject_io(conn,&io);}
    ble_link_disconnect();
}

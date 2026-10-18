#include "ws_client.h"
#include "esp_websocket_client.h"
#include "audio_stream.h"
#include "app.h"
#include "esp_log.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>
static esp_websocket_client_handle_t client;
static bool ready;
static char json_buffer[2048];
static size_t json_received;
static int current_opcode;
static void event(void *args,esp_event_base_t base,int32_t id,void *data_ptr){
    (void)args;(void)base;esp_websocket_event_data_t *d=data_ptr;
    switch(id){
    case WEBSOCKET_EVENT_CONNECTED:ready=false;break;
    case WEBSOCKET_EVENT_DISCONNECTED:ready=false;app_link_status(1,false);break;
    case WEBSOCKET_EVENT_DATA:
        if(d->op_code)current_opcode=d->op_code;
        if(current_opcode==1){
            if(d->payload_offset==0)json_received=0;
            if(d->payload_len>=sizeof(json_buffer)||json_received+d->data_len>=sizeof(json_buffer))return;
            memcpy(json_buffer+json_received,d->data_ptr,d->data_len);json_received+=d->data_len;
            if(d->payload_offset+d->data_len==d->payload_len){
                json_buffer[json_received]=0;
                cJSON *r=cJSON_Parse(json_buffer),*t=cJSON_GetObjectItem(r,"type");
                if(cJSON_IsString(t)&&!strcmp(t->valuestring,"hello"))ready=true;
                cJSON_Delete(r);app_receive_json(json_buffer,json_received,1);
            }
        }else if(current_opcode==2){
            if(audio_stream_play_chunk(d->data_ptr,d->data_len)!=ESP_OK){
                ESP_LOGE("wifi_audio","Playback queue overflow");ready=false;app_link_status(1,false);
            }
        }break;
    case WEBSOCKET_EVENT_ERROR:ESP_LOGW("wifi_audio","Transport error");break;
    default:break;
    }
}
esp_err_t ws_client_start(const char *host,int port){
    if(client)return ESP_OK;
    char uri[256];snprintf(uri,sizeof(uri),"ws://%s:%d/ws/device?id=%s&host=%s",host,port,app_device_id(),app_settings.host_id);
    ESP_LOGI("wifi_audio","Starting WebSocket to %s",uri);
    esp_websocket_client_config_t cfg={.uri=uri,.buffer_size=2048,.reconnect_timeout_ms=5000,.network_timeout_ms=10000,.task_stack=4096};
    client=esp_websocket_client_init(&cfg);if(!client)return ESP_ERR_NO_MEM;
    esp_websocket_register_events(client,WEBSOCKET_EVENT_ANY,event,NULL);return esp_websocket_client_start(client);
}
bool ws_client_send_json(const char *text){return client&&ready&&esp_websocket_client_send_text(client,text,strlen(text),pdMS_TO_TICKS(1000))>=0;}
void ws_client_send_ptt(bool active){ws_client_send_json(active?"{\"type\":\"start\"}":"{\"type\":\"stop\"}");}
void ws_client_send_audio(const void *pcm,size_t len){if(client&&ready&&esp_websocket_client_send_bin(client,pcm,len,pdMS_TO_TICKS(1000))<0){ready=false;app_link_status(1,false);}}
bool ws_client_is_connected(void){return ready;}
void ws_client_stop(void){if(client){ready=false;esp_websocket_client_stop(client);esp_websocket_client_destroy(client);client=NULL;app_link_status(1,false);}}

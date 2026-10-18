#include "audio_stream.h"
#include "bsp_audio.h"
#include "transport.h"
#include "app.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <string.h>
#include <stdlib.h>
#define CHUNK_SIZE 1024
static QueueHandle_t ptt_events,playback,uplink;
static TaskHandle_t record_handle,play_handle,send_handle;
static volatile bool stopping,playing;
static volatile unsigned play_generation;
typedef struct {size_t n;unsigned generation;uint8_t data[CHUNK_SIZE];} play_chunk_t;
typedef struct {
    int kind;
    union {uint8_t bytes[CHUNK_SIZE];int16_t samples[CHUNK_SIZE/2];} audio;
} uplink_t;
static volatile bool capture_failed;
static volatile unsigned uplink_full_count,send_failure_count;
static bool enqueue(uplink_t *packet,int kind){
    packet->kind=kind;
    if(xQueueSend(uplink,packet,0)==pdTRUE)return true;
    uplink_full_count++;capture_failed=true;app_set_state(UI_STATE_ERROR,"Uplink buffer full. Repeat.");return false;
}
static void send_task(void *arg){
    /* Only this singleton task owns the dequeue buffer. Keeping a 1 KiB
     * packet on its stack left too little room for BLE and stdio logging. */
    (void)arg;static uplink_t packet;
    while(!stopping){
        if(xQueueReceive(uplink,&packet,pdMS_TO_TICKS(100))!=pdTRUE)continue;
        if(capture_failed){transport_abort();xQueueReset(uplink);capture_failed=false;continue;}
        bool ok=packet.kind==1?transport_send_ptt(true):packet.kind==3?transport_send_ptt(false):transport_send_audio(packet.audio.bytes,CHUNK_SIZE);
        if(!ok){send_failure_count++;transport_abort();xQueueReset(uplink);audio_stream_set_recording(false);app_set_state(UI_STATE_ERROR,"Audio link lost. Please repeat.");}
    }
    send_handle=NULL;vTaskDelete(NULL);
}
static void record_task(void *arg){
    /* The capture task owns this producer buffer. The queue copies it before
     * the next read; the sender has its own buffer. Avoid a second 1 KiB copy
     * in enqueue(), especially when its full-queue error formats UI text. */
    (void)arg;static uplink_t packet;bool recording=false;int64_t started=0;
    while(!stopping){
        bool active;
        while(xQueueReceive(ptt_events,&active,0)==pdTRUE){
            if(active&&!recording&&transport_connected()){
                if(enqueue(&packet,1)){recording=true;started=esp_timer_get_time();app_set_state(UI_STATE_LISTENING,NULL);}
            }else if(!active&&recording){
                recording=false;if(enqueue(&packet,3))app_set_state(UI_STATE_THINKING,NULL);
            }
        }
        if(!transport_connected())recording=false;
        if(recording&&esp_timer_get_time()-started>=30000000){recording=false;enqueue(&packet,3);app_set_state(UI_STATE_THINKING,NULL);}
        esp_err_t err=bsp_audio_read(packet.audio.samples,sizeof(packet.audio.samples));
        if(err==ESP_OK&&recording){
            int peak=0;for(int i=0;i<CHUNK_SIZE/2;i++){int a=abs((int)packet.audio.samples[i]);if(a>peak)peak=a;}
            app_level(peak*100/32768);
            if(!enqueue(&packet,2)){recording=false;app_set_state(UI_STATE_ERROR,"Audio link interrupted");}
        }else if(err!=ESP_OK)vTaskDelay(pdMS_TO_TICKS(10));
    }
    record_handle=NULL;vTaskDelete(NULL);
}
static void play_task(void *arg){
    (void)arg;play_chunk_t chunk;bool primed=false;
    while(!stopping){
        if(xQueueReceive(playback,&chunk,pdMS_TO_TICKS(100))!=pdTRUE){primed=false;continue;}
        unsigned generation=chunk.generation;
        playing=true;app_activity();
        if(!primed){
            int64_t until=esp_timer_get_time()+80000;
            while(!stopping&&uxQueueMessagesWaiting(playback)==0&&esp_timer_get_time()<until)
                vTaskDelay(pdMS_TO_TICKS(5));
        }
        if(generation!=play_generation){playing=false;primed=false;continue;}
        primed=true;bsp_audio_write(chunk.data,chunk.n);playing=false;app_activity();
    }
    play_handle=NULL;vTaskDelete(NULL);
}
esp_err_t audio_stream_init(void){
    ESP_RETURN_ON_ERROR(bsp_audio_init(),"audio","codec");
    ESP_RETURN_ON_ERROR(bsp_audio_set_format(16000,16,1),"audio","format");
    bsp_audio_set_volume(app_settings.volume);
    ptt_events=xQueueCreate(8,sizeof(bool));playback=xQueueCreate(4,sizeof(play_chunk_t));uplink=xQueueCreate(6,sizeof(uplink_t));
    if(!ptt_events||!playback||!uplink)return ESP_ERR_NO_MEM;
    if(xTaskCreate(send_task,"audio_send",2048,NULL,4,&send_handle)!=pdPASS)return ESP_ERR_NO_MEM;
    if(xTaskCreate(record_task,"capture",2048,NULL,5,&record_handle)!=pdPASS)return ESP_ERR_NO_MEM;
    if(xTaskCreate(play_task,"playback",2048,NULL,5,&play_handle)!=pdPASS)return ESP_ERR_NO_MEM;
    return ESP_OK;
}
void audio_stream_set_recording(bool active){if(!stopping&&ptt_events&&xQueueSend(ptt_events,&active,0)!=pdTRUE)app_set_state(UI_STATE_ERROR,"Button queue overflow");}
esp_err_t audio_stream_play_chunk(const void *pcm,size_t bytes){
    if(stopping||!playback)return ESP_ERR_INVALID_STATE;
    const uint8_t *p=pcm;play_chunk_t chunk;
    while(bytes){chunk.generation=play_generation;chunk.n=bytes>CHUNK_SIZE?CHUNK_SIZE:bytes;memcpy(chunk.data,p,chunk.n);
        if(xQueueSend(playback,&chunk,pdMS_TO_TICKS(500))!=pdTRUE)return ESP_ERR_TIMEOUT;
        p+=chunk.n;bytes-=chunk.n;}
    return ESP_OK;
}
bool audio_stream_playing(void){return playing||(playback&&uxQueueMessagesWaiting(playback));}
void audio_stream_log_stack_usage(void){
#if CONFIG_TALK2GEMI_USB_DIAGNOSTICS
    if(send_handle&&record_handle&&play_handle){
        ESP_LOGI("audio_stack","minimum free bytes: send=%u capture=%u playback=%u uplink_full=%u send_fail=%u",
                 (unsigned)uxTaskGetStackHighWaterMark(send_handle),
                 (unsigned)uxTaskGetStackHighWaterMark(record_handle),
                 (unsigned)uxTaskGetStackHighWaterMark(play_handle),uplink_full_count,send_failure_count);
    }
#endif
}
void audio_stream_stop(void){
    stopping=true;
    while(record_handle||play_handle||send_handle)vTaskDelay(pdMS_TO_TICKS(20));
    if(ptt_events){QueueHandle_t q=ptt_events;ptt_events=NULL;vQueueDelete(q);}
    if(playback){QueueHandle_t q=playback;playback=NULL;vQueueDelete(q);}
    if(uplink){QueueHandle_t q=uplink;uplink=NULL;vQueueDelete(q);}
}

void audio_stream_flush_playback(void){play_generation++;if(playback)xQueueReset(playback);}

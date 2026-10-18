#include "transport.h"
#include "app.h"
#include "ble_link.h"
#include "ws_client.h"
#include "wifi_portal.h"
#include "audio_stream.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include <stdio.h>
#include <string.h>
static QueueHandle_t controls;
static volatile int active;
static volatile bool stopped,force_reconnect;
static bool turn_open;
static volatile bool forget_wifi;
static void task(void *arg){
    (void)arg;
    int64_t boot=esp_timer_get_time(),next_ble=0;
    int backoff=5;
    bool wifi_started=false;
    char wifi_host[64]="";
    int wifi_port=0;
    int64_t next_wifi=0;
    int wifi_backoff=5;
    int wifi_fail_count=0;

    if(!wifi_portal_has_config() && !app_settings.token[0]){
        wifi_portal_start_ap_web();
    }

    while(!stopped){
        if(forget_wifi){
            forget_wifi=false;
            ws_client_stop();wifi_started=false;active=0;turn_open=false;
            audio_stream_set_recording(false);
            esp_err_t err=wifi_portal_forget_sta();
            if(err==ESP_OK){
                app_settings.preference=2;app_settings.last_transport=2;app_save();
                wifi_portal_stop();
                if(!ble_link_is_active()) ble_link_init();
                ble_link_reconnect();
                app_set_state(UI_STATE_BOOT,"Connecting Bluetooth...");
            }else{
                app_set_state(UI_STATE_ERROR,"Wi-Fi forget failed");
            }
            printf("TG wifi forget result=%d configured=%d\n",(int)err,wifi_portal_has_config());
        }

        char control[256];
        while(xQueueReceive(controls,control,0)==pdTRUE){
            if(active==1)ws_client_send_json(control);
            else if(active==2)ble_link_send_json(control);
        }

        int64_t now=esp_timer_get_time();

        if(wifi_portal_is_setup()){
            if(ble_link_is_active()) ble_link_stop();
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if(app_selecting_mac()){
            if(wifi_started){ ws_client_stop(); wifi_started=false; }
            wifi_portal_stop();
            if(!ble_link_is_active()) ble_link_init();
            vTaskDelay(pdMS_TO_TICKS(250));
            continue;
        }

        if(force_reconnect){
            force_reconnect=false;
            ws_client_stop();active=0;wifi_started=false;
            next_ble=0;next_wifi=0;backoff=5;wifi_backoff=5;wifi_fail_count=0;
        }

        int pref=app_settings.preference; // 0: Auto, 1: Wi-Fi, 2: BLE
        bool has_wifi=wifi_portal_has_config();
        bool has_ble=(app_settings.token[0] != 0);

        if(pref == 2 && has_ble){
            // Explicit BLE preference: pause Wi-Fi, activate BLE
            if(wifi_started){ ws_client_stop(); wifi_started=false; }
            wifi_portal_stop();
            if(!ble_link_is_active()) ble_link_init();
            if(!ble_link_connected() && now>=next_ble){
                ble_link_reconnect();
                next_ble=now+(int64_t)backoff*1000000;
                backoff=backoff<60?backoff*2:60;
            }
        }else if(has_wifi && wifi_fail_count < 2){
            // Primary path: Wi-Fi prioritized
            wifi_ap_record_t ap;
            bool wifi_up=esp_wifi_sta_get_ap_info(&ap)==ESP_OK && wifi_portal_get_sta_ip()[0];
            if(!wifi_up && now>=next_wifi){
                if(wifi_portal_connect_sta(10)==ESP_OK){
                    wifi_up=true;
                    wifi_backoff=5;
                }else{
                    wifi_fail_count++;
                    next_wifi=now+(int64_t)wifi_backoff*1000000;
                    wifi_backoff=wifi_backoff<60?wifi_backoff*2:60;
                }
            }
            if(wifi_up && !wifi_started){
                snprintf(wifi_host,sizeof(wifi_host),"%s",wifi_portal_get_server_ip());
                wifi_port=wifi_portal_get_server_port();
                ws_client_start(wifi_host,wifi_port);
                wifi_started=true;
                wifi_backoff=5;
            }
            if(ws_client_is_connected()){
                wifi_fail_count=0;
                // Wi-Fi is active and healthy: pause BLE to keep full RAM free
                if(ble_link_is_active()){
                    ble_link_stop();
                }
            }
        }else if(has_ble){
            // Fallback path: Wi-Fi unavailable or no Wi-Fi config -> Enable BLE, pause Wi-Fi
            if(wifi_started){ ws_client_stop(); wifi_started=false; }
            wifi_portal_stop();
            if(!ble_link_is_active()){
                ble_link_init();
            }
            if(!ble_link_connected() && now>=next_ble){
                ble_link_reconnect();
                next_ble=now+(int64_t)backoff*1000000;
                backoff=backoff<60?backoff*2:60;
            }
            // Periodically check if Wi-Fi has recovered (every 60s)
            if(has_wifi && pref != 2 && now>=next_wifi+60000000){
                wifi_fail_count=0;
                next_wifi=now;
            }
        }

        bool w=ws_client_is_connected();
        bool b=ble_link_connected();

        if(active&&((active==1&&!w)||(active==2&&!b))){
            active=0;turn_open=false;audio_stream_set_recording(false);
            app_set_state(UI_STATE_ERROR,"Link lost. Please repeat.");
        }

        if(!active&&!app_busy()){
            int chosen=0;
            if(w) chosen=1;
            else if(b) chosen=2;
            if(chosen){
                bool ok=chosen==1?ws_client_send_json("{\"type\":\"activate\"}"):ble_link_send_json("{\"type\":\"activate\"}");
                if(ok){
                    active=chosen;
                    app_settings.last_transport=chosen;
                    app_save();
                    backoff=5;
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(250));
    }
    vTaskDelete(NULL);
}
void transport_init(void){controls=xQueueCreate(4,256);assert(controls);xTaskCreate(task,"connections",4096,NULL,3,NULL);}
bool transport_send_control(const char *json){
    char data[256];if(!controls||strlen(json)>=sizeof(data))return false;
    snprintf(data,sizeof(data),"%s",json);return xQueueSend(controls,data,0)==pdTRUE;
}
bool transport_connected(void){return active==1?ws_client_is_connected():active==2?ble_link_connected():false;}
int transport_active(void){return active;}
bool transport_send_ptt(bool start){
    if(!transport_connected())return false;
    const char *msg=start?"{\"type\":\"start\"}":"{\"type\":\"stop\"}";
    bool ok=active==1?ws_client_send_json(msg):ble_link_send_json(msg);turn_open=start&&ok;return ok;
}
bool transport_send_audio(const void *pcm,size_t len){
    if(!turn_open||!transport_connected())return false;
    if(active==1){ws_client_send_audio(pcm,len);return ws_client_is_connected();}
    return ble_link_send_audio(pcm,len);
}
void transport_abort(void){
    turn_open=false;
    if(active==2)ble_link_disconnect();
    force_reconnect=true;active=0;
}
void transport_reconnect(void){force_reconnect=true;}
void transport_stop(void){stopped=true;ws_client_stop();ble_link_stop();esp_wifi_stop();}

bool transport_forget_wifi(void){if(app_busy()||forget_wifi||wifi_portal_is_setup())return false;forget_wifi=true;return true;}

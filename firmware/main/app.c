#include "app.h"
#include "idle_policy.h"
#include "ble_link.h"
#include "transport.h"
#include "audio_stream.h"
#include "wifi_portal.h"
#include "bsp_display.h"
#include "bsp_audio.h"
#include "bsp_battery.h"
#include "bsp_button.h"
#include "bsp_i2c.h"
#include "bsp_pins.h"
#include "esp_lvgl_port.h"
#include "driver/usb_serial_jtag.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "nvs.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
app_settings_t app_settings={.volume=70,.brightness=70};
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static SemaphoreHandle_t save_lock;
static QueueHandle_t keys;
static ui_state_t state=UI_STATE_BOOT;
static char message[96]="Starting systems",device_id[20],voice[24]="--",model[64]="--";
static bool links[3],cloud,long_key;
static idle_policy_t power;
#define screen_off power.screen_off
#define wake_guard power.wake_guard
#define usb_host power.usb
#define last_activity power.last_activity
#define sleep_anchor power.sleep_anchor
static bool volume_dirty;
static bool selecting_mac;
static int64_t pairing_until;
static bool pairing_wake;
bool app_selecting_mac(void){return selecting_mac;}
static int level,battery=-1,latency=-1;
static int64_t state_since,volume_until;
static int page,selection,status_page;
static const char *titles[]={"","STATUS","SETTINGS","CONNECTION","BLUETOOTH","SELECT MAC","AUDIO","DISPLAY","POWER","ABOUT","FORGET WI-FI?","FORGET BLUETOOTH?"};
static const char *menus[][7]={
    {NULL},{NULL},{"Connection","Audio","Display","Power","About",NULL},
    {"Prefer: Auto","Wi-Fi Setup","Bluetooth","Reconnect","Forget Wi-Fi","Forget Bluetooth",NULL},
    {"Scan","Reconnect","Change Mac","Forget Device",NULL},{NULL},
    {"Volume","Back",NULL},{"Brightness","Back",NULL},{"Desktop Mode","Back",NULL},{"Back",NULL},{"Cancel","Forget Wi-Fi",NULL},{"Cancel","Forget Bluetooth",NULL}};
typedef struct {bsp_btn_t button;bsp_btn_ev_t event;} key_event_t;
void app_activity(void){portENTER_CRITICAL(&lock);last_activity=esp_timer_get_time();portEXIT_CRITICAL(&lock);}
void app_save(void){
    if(!save_lock||xSemaphoreTake(save_lock,pdMS_TO_TICKS(1000))!=pdTRUE)return;
    nvs_handle_t n;if(nvs_open("t2g_v2",NVS_READWRITE,&n)==ESP_OK){nvs_set_blob(n,"settings",&app_settings,sizeof(app_settings));nvs_commit(n);nvs_close(n);}
    xSemaphoreGive(save_lock);
}
const char *app_device_id(void){return device_id;}
void app_set_state(ui_state_t value,const char *detail){
    portENTER_CRITICAL(&lock);
    bool was_busy=state==UI_STATE_LISTENING||state==UI_STATE_THINKING||state==UI_STATE_SPEAKING;
    state=value;state_since=esp_timer_get_time();
    /* Audio tasks also report failures here; avoid printf's stack overhead. */
    strlcpy(message,detail?detail:"",sizeof(message));
    if(was_busy||value==UI_STATE_LISTENING||value==UI_STATE_SPEAKING)last_activity=state_since;
    portEXIT_CRITICAL(&lock);
}
void app_level(int value){level=value;}
bool app_busy(void){return state==UI_STATE_LISTENING||state==UI_STATE_THINKING||state==UI_STATE_SPEAKING||audio_stream_playing();}
void app_link_status(int transport,bool connected){if(transport>=1&&transport<=2)links[transport]=connected;if(transport==2&&!connected)pairing_until=0;}
static const char *str(cJSON *r,const char *key){cJSON *v=cJSON_GetObjectItem(r,key);return cJSON_IsString(v)?v->valuestring:"";}
void app_receive_json(const char *data,int length,int channel){
    cJSON *r=cJSON_ParseWithLength(data,length);if(!r)return;
    const char *type=str(r,"type");
    if(!strcmp(type,"hello")||!strcmp(type,"paired")){
        if(channel==2)pairing_until=0;
        const char *host=str(r,"host_id");
        if(app_settings.host_id[0]&&strcmp(app_settings.host_id,host)){
            app_set_state(UI_STATE_ERROR,"Different Mac: Change Mac first");cJSON_Delete(r);return;
        }
        snprintf(app_settings.host_id,sizeof(app_settings.host_id),"%s",host);
        snprintf(app_settings.mac_name,sizeof(app_settings.mac_name),"%s",str(r,"name"));
        if(!strcmp(type,"paired"))snprintf(app_settings.token,sizeof(app_settings.token),"%s",str(r,"token"));
        else {
            cJSON *port=cJSON_GetObjectItem(r,"relay_port");
            if(channel==2&&cJSON_IsNumber(port))wifi_portal_set_server(str(r,"relay_ip"),port->valueint);
            app_link_status(channel,true);snprintf(voice,sizeof(voice),"%s",str(r,"voice"));snprintf(model,sizeof(model),"%s",str(r,"model"));
            selecting_mac=false;
        }
        app_save();
    }else if(!strcmp(type,"pair_code")||!strcmp(type,"system_pair_code")||!strcmp(type,"compare_pair_code")){
        ESP_LOGI("pairing","Compare with Mac: %s",str(r,"code"));
        const char *hint=!strcmp(type,"system_pair_code")?"Enter this code on your Mac":!strcmp(type,"compare_pair_code")?"Same on Mac? OK=Yes UP=No":"Confirm on your Mac";
        char text[96];snprintf(text,sizeof(text),"%s: %s\n%s",!strcmp(type,"pair_code")?"Mac link code":"System code",str(r,"code"),hint);app_set_state(UI_STATE_BOOT,text);page=0;
        pairing_until=esp_timer_get_time()+60000000;pairing_wake=true;app_activity();
    }else if(!strcmp(type,"state")&&(channel==transport_active()||!transport_active())){
        if(pairing_until>esp_timer_get_time()){cJSON_Delete(r);return;}
        const char *v=str(r,"value");
        if(!strcmp(v,"ready")||!strcmp(v,"idle")){cloud=true;app_set_state(UI_STATE_READY,NULL);}
        else if(!strcmp(v,"listening"))app_set_state(UI_STATE_LISTENING,NULL);
        else if(!strcmp(v,"thinking"))app_set_state(UI_STATE_THINKING,str(r,"reason"));
        else if(!strcmp(v,"speaking"))app_set_state(UI_STATE_SPEAKING,NULL);
        else if(!strcmp(v,"error")){cloud=false;app_set_state(UI_STATE_ERROR,str(r,"reason"));}
    }else if(!strcmp(type,"audio_abort")){
        audio_stream_flush_playback();
    }else if(!strcmp(type,"metrics")){
        cloud=cJSON_IsTrue(cJSON_GetObjectItem(r,"cloud"));
        cJSON *v=cJSON_GetObjectItem(r,"latency");latency=cJSON_IsNumber(v)?v->valueint:-1;
        snprintf(voice,sizeof(voice),"%s",str(r,"voice"));snprintf(model,sizeof(model),"%s",str(r,"model"));
    }else if(!strcmp(type,"volume")){
        cJSON *v=cJSON_GetObjectItem(r,"value");
        if(cJSON_IsNumber(v)&&v->valueint>=0&&v->valueint<=100){app_settings.volume=v->valueint;volume_dirty=true;app_activity();}
    }else if(!strcmp(type,"error"))app_set_state(UI_STATE_ERROR,str(r,"reason"));
    cJSON_Delete(r);
}
void app_button(bsp_btn_t button,bsp_btn_ev_t event,void *user){
    (void)user;key_event_t e={button,event};if(keys)xQueueSend(keys,&e,0);
}
static void volume_change(int delta){
    int value=app_settings.volume+delta;if(value<0)value=0;if(value>100)value=100;
    app_settings.volume=value;volume_dirty=true;volume_until=esp_timer_get_time()+1500000;
}
static void home(void){page=0;selection=0;}
static void forget_host(void){
    selecting_mac=true;
    ble_link_forget();app_settings.host_id[0]=app_settings.mac_name[0]=app_settings.token[0]=0;
    app_save();transport_reconnect();
}
static void confirm(void){
    if(page==1||page==9){home();return;}
    if(page==2){static const int pages[]={3,6,7,8,9};page=pages[selection];selection=0;return;}
    if(page==3){
        if(selection==0){app_settings.preference=(app_settings.preference+1)%3;app_save();}
        else if(selection==1){wifi_portal_start_ap_web();home();}
        else if(selection==2){page=4;selection=0;}
        else if(selection==3)transport_reconnect();
        else {page=selection==4?10:11;selection=0;}
    }else if(page==10||page==11){
        if(selection==0){page=3;selection=0;return;}
        if(app_busy()){home();app_set_state(UI_STATE_ERROR,"Finish audio before forgetting");return;}
        if(page==10){
            bool queued=transport_forget_wifi();home();
            if(!queued)app_set_state(UI_STATE_ERROR,"Cannot forget Wi-Fi now");
        }else{forget_host();home();app_set_state(UI_STATE_READY,"Bluetooth forgotten. Select a Mac.");}
    }else if(page==4){
        if(selection==0){page=5;selection=0;ble_link_scan();}
        else if(selection==1)ble_link_reconnect();
        else if(selection==2){forget_host();page=5;selection=0;ble_link_scan();}
        else forget_host();
    }else if(page==5){ble_link_connect(selection);home();app_set_state(UI_STATE_BOOT,"Connecting Bluetooth...");}
    else if(page==6){if(selection==0)volume_change(5);else{page=2;selection=0;}}
    else if(page==7){if(selection==0){app_settings.brightness=app_settings.brightness>=100?10:app_settings.brightness+10;bsp_display_backlight(app_settings.brightness);app_save();}else{page=2;selection=0;}}
    else if(page==8){if(selection==0){app_settings.desktop=!app_settings.desktop;sleep_anchor=esp_timer_get_time();app_save();}else{page=2;selection=0;}}
}
static int menu_count(void){
    if(page==5){ble_candidate_t list[8];return ble_link_candidates(list,8);}
    if(page<2||page>11) { return 1; }
    int n=0;while(n<6&&menus[page][n])n++;return n;
}
static void handle_key(key_event_t key){
    bool was_off=screen_off;
    bool accept=idle_policy_key(&power,key.event==BSP_BTN_PRESS,key.event==BSP_BTN_RELEASE);
    if(was_off&&!screen_off){lvgl_port_resume();bsp_display_backlight(app_settings.brightness);app_activity();}
    if(!accept){if(key.event==BSP_BTN_RELEASE)app_activity();return;}
    if(pairing_until>esp_timer_get_time()){
        if(key.event==BSP_BTN_RELEASE){if(key.button==BSP_BTN_UP){pairing_until=0;ble_link_reject_pairing();}else if(key.button==BSP_BTN_OK)ble_link_confirm_pairing(-1);}
        return;
    }
    if(key.event==BSP_BTN_CLICK||key.event==BSP_BTN_DOUBLE)return;
    app_activity();
    if(key.event==BSP_BTN_PRESS){long_key=false;if(!page&&state!=UI_STATE_WIFI_SETUP&&key.button==BSP_BTN_OK)audio_stream_set_recording(true);return;}
    if(key.event==BSP_BTN_LONG){
        long_key=true;
        if(state==UI_STATE_WIFI_SETUP){if(key.button==BSP_BTN_UP)esp_restart();return;}
        if(key.button==BSP_BTN_UP){if(!page){page=1;status_page=0;}else home();}
        else if(key.button==BSP_BTN_DOWN&&!page){page=2;selection=0;}
        return;
    }
    if(key.event!=BSP_BTN_RELEASE)return;
    if(!page&&state!=UI_STATE_WIFI_SETUP&&key.button==BSP_BTN_OK){audio_stream_set_recording(false);return;}
    if(long_key)return;
    if(!page&&state!=UI_STATE_WIFI_SETUP){if(key.button==BSP_BTN_UP)volume_change(5);if(key.button==BSP_BTN_DOWN)volume_change(-5);}
    else if(!page&&state==UI_STATE_WIFI_SETUP){}
    else if(page==1){if(key.button==BSP_BTN_UP)status_page=(status_page+2)%3;else if(key.button==BSP_BTN_DOWN)status_page=(status_page+1)%3;else home();}
    else if(key.button==BSP_BTN_OK)confirm();
    else {int n=menu_count();if(n>0)selection=(selection+(key.button==BSP_BTN_UP?n-1:1))%n;}
}
static void snapshot(ui_snapshot_t *m){
    memset(m,0,sizeof(*m));m->battery=battery;m->level=level;
    m->latency_ms=latency;m->state=state;m->playing=audio_stream_playing();
    m->idle_ms=(esp_timer_get_time()-last_activity)/1000;m->volume=app_settings.volume;m->volume_visible=esp_timer_get_time()<volume_until;
    snprintf(m->computer,sizeof(m->computer),"%s",app_settings.mac_name[0]?app_settings.mac_name:"No computer");
    wifi_ap_record_t ap;bool wifi=esp_wifi_sta_get_ap_info(&ap)==ESP_OK;
    m->wifi=wifi;m->ble=ble_link_connected();m->relay=transport_connected();m->cloud=cloud&&m->relay;
    m->listening=state==UI_STATE_LISTENING;
    const char *active=transport_active()==1?"Wi-Fi":transport_active()==2?"BLE":"--";
    if(latency<0)snprintf(m->footer,sizeof(m->footer),"%s  |  Reply --\nUP/DOWN VOL  |  OK TALK",active);
    else snprintf(m->footer,sizeof(m->footer),"%s  |  Reply %dms\nUP/DOWN VOL  |  OK TALK",active,latency);
    if(!page){
        const char *title="OFFLINE";
        if(pairing_until>esp_timer_get_time())title="PAIRING";
        else if(state==UI_STATE_BOOT)title="CONNECTING";
        else if(state==UI_STATE_WIFI_SETUP)title="WI-FI SETUP";
        else if(state==UI_STATE_LISTENING)title="LISTENING";
        else if(state==UI_STATE_THINKING)title="THINKING";
        else if(state==UI_STATE_SPEAKING)title="SPEAKING";
        else if(m->relay&&m->cloud&&state==UI_STATE_READY)title="READY";
        snprintf(m->title,sizeof(m->title),"%s",title);m->error=state==UI_STATE_ERROR;
        if(state==UI_STATE_THINKING)snprintf(m->detail,sizeof(m->detail),"%.60s\nWaiting %llds",message[0]?message:"",(esp_timer_get_time()-state_since)/1000000);
        else snprintf(m->detail,sizeof(m->detail),"%s",message[0]?message:m->relay?"Hold OK to talk":"Long DOWN for settings");
        if(esp_timer_get_time()<volume_until)snprintf(m->body,sizeof(m->body),"VOLUME  %d%%",app_settings.volume);
        else snprintf(m->body,sizeof(m->body),"%s\n%s",app_settings.mac_name[0]?app_settings.mac_name:"No Mac selected",usb_host?"USB host detected":app_settings.desktop?"Desktop Mode":"Battery / charger");
        return;
    }
    m->menu=true;snprintf(m->title,sizeof(m->title),"%s",titles[page]);
    snprintf(m->detail,sizeof(m->detail),"UP/DOWN select  |  OK confirm");
    snprintf(m->footer,sizeof(m->footer),"Hold UP to return home");
    if(page==1){
        snprintf(m->detail,sizeof(m->detail),"%d/3  |  UP/DOWN page",status_page+1);
        if(status_page==0)snprintf(m->body,sizeof(m->body),"Wi-Fi: %.24s\nIP: %s\nRSSI: %d dBm\nBLE: %.24s\nBLE RSSI: %s%d\nRelay: %s:%d",wifi_portal_get_ssid(),wifi_portal_get_sta_ip(),wifi?ap.rssi:0,app_settings.mac_name,ble_link_connected()?"":"-- ",ble_link_rssi(),wifi_portal_get_server_ip(),wifi_portal_get_server_port());
        else if(status_page==1)snprintf(m->body,sizeof(m->body),"Model: %.32s\nVoice: %s\nChannel: %s\nCloud: %s\nVersion: %s",model,voice,active,cloud?"Ready":"Offline",esp_app_get_description()->version);
        else snprintf(m->body,sizeof(m->body),"Uptime: %llds\nUSB host: %s\nDesktop: %s\nScreen off: 5 min\nBattery sleep: 1 hour\nFree heap: %u\nLargest block: %u",esp_timer_get_time()/1000000,usb_host?"Yes":"No",app_settings.desktop?"On":"Off",(unsigned)esp_get_free_heap_size(),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    }else if(page==5){
        ble_candidate_t list[8];int n=ble_link_candidates(list,8);if(!n)snprintf(m->body,sizeof(m->body),"Scanning for Mac...\nStart Mac companion first.");
        for(int i=0;i<n&&i<6;i++){size_t used=strlen(m->body);snprintf(m->body+used,sizeof(m->body)-used,"%s %.20s %d\n",selection==i?">":" ",list[i].name,list[i].rssi);}
    }else if(page==9)snprintf(m->body,sizeof(m->body),"talk2gemi %s\nDevice: %s\nEnglish interface\n5 min screen off\n1 hour battery sleep\nLong UP: status\nLong DOWN: settings",esp_app_get_description()->version,device_id);
    else for(int i=0;i<menu_count();i++){
        char text[96];snprintf(text,sizeof(text),"%s",menus[page][i]);
        if(page==3&&i==0)snprintf(text,sizeof(text),"Prefer: %s",app_settings.preference==1?"Wi-Fi":app_settings.preference==2?"BLE":"Auto");
        if(page==6&&i==0)snprintf(text,sizeof(text),"Volume: %d%% (+5)",app_settings.volume);
        if(page==7&&i==0)snprintf(text,sizeof(text),"Brightness: %d%%",app_settings.brightness);
        if(page==8&&i==0)snprintf(text,sizeof(text),"Desktop Mode: %s",app_settings.desktop?"On":"Off");
        size_t n=strlen(m->body);snprintf(m->body+n,sizeof(m->body)-n,"%s %s\n",selection==i?">":" ",text);
    }
}
void app_prepare_sleep(void){
    int mv=bsp_button_read_mv();if(mv<0||mv<BSP_BTN_MV_RELEASED_MIN)return;
    audio_stream_stop();transport_stop();
    lvgl_port_stop();
    ESP_ERROR_CHECK(bsp_audio_prepare_deep_sleep());
    ESP_ERROR_CHECK(bsp_battery_sleep());
    ESP_ERROR_CHECK(bsp_display_prepare_deep_sleep());
    ESP_ERROR_CHECK(bsp_i2c_prepare_deep_sleep());
    int level;ESP_ERROR_CHECK(bsp_button_prepare_deep_sleep(&level));
    ESP_ERROR_CHECK(esp_deep_sleep_enable_gpio_wakeup(1ULL<<BSP_BTN_GPIO,ESP_GPIO_WAKEUP_GPIO_LOW));
    esp_deep_sleep_start();
}
static void app_task(void *arg){
    (void)arg;int64_t sampled=0;ui_snapshot_t m;
    while(1){
        key_event_t e;while(xQueueReceive(keys,&e,0)==pdTRUE)handle_key(e);
        if(pairing_wake){pairing_wake=false;if(screen_off){screen_off=false;lvgl_port_resume();bsp_display_backlight(app_settings.brightness);}}
        if(volume_dirty){volume_dirty=false;bsp_audio_set_volume(app_settings.volume);app_save();}
        int64_t now=esp_timer_get_time();bool host=usb_serial_jtag_is_connected();
        idle_policy_usb(&power,host,now);
        if(now-sampled>=5000000){sampled=now;battery=bsp_battery_soc();
            char telemetry[256];snprintf(telemetry,sizeof(telemetry),"{\"type\":\"telemetry\",\"volume\":%d,\"battery\":%d,\"usb_host\":%s,\"desktop\":%s,\"heap\":%u,\"largest\":%u}",app_settings.volume,battery,usb_host?"true":"false",app_settings.desktop?"true":"false",(unsigned)esp_get_free_heap_size(),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
            transport_send_control(telemetry);
            ESP_LOGI("health","heap=%u largest=%u min=%u wifi=%d ble=%d active=%d usb=%d",(unsigned)esp_get_free_heap_size(),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),(unsigned)esp_get_minimum_free_heap_size(),links[1],links[2],transport_active(),usb_host);
            audio_stream_log_stack_usage();}
        power.desktop=app_settings.desktop;
        int actions=idle_policy_step(&power,now,app_busy());
        if(actions&1){bsp_display_backlight(0);lvgl_port_stop();ESP_LOGI("power","Screen off after 5 minutes");}
        if(actions&2)app_prepare_sleep();
        if(!screen_off){snapshot(&m);ui_render(&m);}
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
void app_init(void){
    save_lock=xSemaphoreCreateMutex();keys=xQueueCreate(24,sizeof(key_event_t));assert(save_lock&&keys);
    nvs_handle_t n;if(nvs_open("t2g_v2",NVS_READONLY,&n)==ESP_OK){size_t size=sizeof(app_settings);nvs_get_blob(n,"settings",&app_settings,&size);nvs_close(n);}
    if(app_settings.volume<0||app_settings.volume>100)app_settings.volume=70;
    if(app_settings.brightness<10||app_settings.brightness>100)app_settings.brightness=70;
    if(app_settings.preference<0||app_settings.preference>2)app_settings.preference=0;
    app_settings.host_id[39]=app_settings.mac_name[63]=app_settings.token[63]=0;
    uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);snprintf(device_id,sizeof(device_id),"%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
    last_activity=sleep_anchor=state_since=esp_timer_get_time();wake_guard=esp_sleep_get_wakeup_cause()==ESP_SLEEP_WAKEUP_GPIO;
    if(xTaskCreate(app_task,"app_ui",4096,NULL,2,NULL)!=pdPASS)abort();
}

#include "sdkconfig.h"
#include "app.h"
#include "ble_link.h"
#include "transport.h"
#include "wifi_portal.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#if CONFIG_TALK2GEMI_USB_DIAGNOSTICS
static bool injecting;
static void command(char *line){
    if(!strcmp(line,"tg scan")){ble_link_scan();puts("TG scan started");}
    else if(!strcmp(line,"tg list")){
        ble_candidate_t list[8];int n=ble_link_candidates(list,8);
        printf("TG candidates %d\n",n);
        for(int i=0;i<n;i++)printf("TG mac %d %s %d\n",i,list[i].name,list[i].rssi);
    }else if(!strncmp(line,"tg connect ",11)){ble_link_connect(atoi(line+11));puts("TG connect requested");}
    else if(!strncmp(line,"tg confirm ",11)){printf("TG pairing confirmed %d\n",ble_link_confirm_pairing(atoi(line+11)));}
    else if(!strcmp(line,"tg wifi forget"))printf("TG wifi forget queued=%d\n",transport_forget_wifi());
    else if(!strcmp(line,"tg wifi status"))printf("TG wifi configured=%d connected=%d\n",wifi_portal_has_config(),wifi_portal_get_sta_ip()[0]!=0);
    else if(!strcmp(line,"tg transport ble")){app_settings.preference=2;app_save();transport_reconnect();puts("TG prefer BLE");}
    else if(!strcmp(line,"tg transport wifi")){app_settings.preference=1;app_save();transport_reconnect();puts("TG prefer Wi-Fi");}
    else if(!strcmp(line,"tg key up")){app_button(BSP_BTN_UP,BSP_BTN_PRESS,NULL);app_button(BSP_BTN_UP,BSP_BTN_RELEASE,NULL);puts("TG synthetic UP queued");}
    else if(!strcmp(line,"tg start-test")){
        if(!app_busy()&&transport_connected()){
            injecting=transport_send_ptt(true);if(injecting)app_set_state(UI_STATE_LISTENING,"USB supplied test audio");
        }
        printf("TG test start %d\n",injecting);
    }else if(!strcmp(line,"tg stop-test")){
        if(injecting){injecting=false;transport_send_ptt(false);app_set_state(UI_STATE_THINKING,NULL);}
        puts("TG test stopped");
    }else if(!strncmp(line,"tg pcm ",7)&&injecting){
        size_t n=strlen(line+7);uint8_t pcm[1024];bool valid=n>0&&n<=2048&&n%4==0;
        for(size_t i=0;valid&&i<n/2;i++){
            char hex[3]={line[7+i*2],line[8+i*2],0};char *end=NULL;
            long value=strtol(hex,&end,16);if(*end||value<0||value>255)valid=false;else pcm[i]=value;
        }
        if(valid&&transport_send_audio(pcm,n/2))puts("TG pcm accepted");else puts("TG pcm failed");
    }else if(!strcmp(line,"tg status"))printf("TG status channel=%d ble=%d busy=%d volume=%d host=%s\n",transport_active(),ble_link_connected(),app_busy(),app_settings.volume,app_settings.host_id);
}
static void task(void *arg){
    (void)arg;static char line[2100];size_t length=0;
    while(1){
        char input[128];int n=read(STDIN_FILENO,input,sizeof(input));
        for(int i=0;i<n;i++){
            char c=input[i];
            if(c=='\n'){line[length]=0;command(line);length=0;}
            else if(c!='\r'){if(length<sizeof(line)-1)line[length++]=c;else length=0;}
        }
        if(n<=0)vTaskDelay(pdMS_TO_TICKS(10));
    }
}
void usb_diagnostics_start(void){xTaskCreate(task,"usb_tests",4096,NULL,2,NULL);}
#else
void usb_diagnostics_start(void){}
#endif

#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "ui.h"
#include "bsp_button.h"
typedef struct {
    int volume, brightness, preference, last_transport;
    bool desktop;
    char host_id[40], mac_name[64], token[64];
} app_settings_t;
extern app_settings_t app_settings;
void app_init(void);
void app_button(bsp_btn_t btn,bsp_btn_ev_t ev,void *user);
void app_activity(void);
void app_set_state(ui_state_t state,const char *detail);
void app_level(int value);
bool app_busy(void);
void app_receive_json(const char *data,int length,int transport);
void app_save(void);
void app_link_status(int transport,bool connected);
const char *app_device_id(void);
void app_prepare_sleep(void);
bool app_selecting_mac(void);

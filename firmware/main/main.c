#include "app.h"
#include "ble_link.h"
#include "transport.h"
#include "audio_stream.h"
#include "wifi_portal.h"
#include "bsp_battery.h"
#include "bsp_button.h"
#include "nvs_flash.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
static void wifi_status(wifi_portal_status_t status,const char *message){
    if(status==WIFI_STATUS_AP_MODE)app_set_state(UI_STATE_WIFI_SETUP,message);
    else if(status==WIFI_STATUS_CONNECTING&&!transport_connected())app_set_state(UI_STATE_WIFI_CONNECTING,message);
    else if(status==WIFI_STATUS_CONNECTED&&!transport_connected())app_set_state(UI_STATE_BOOT,"Connecting to Mac...");
    else if(status==WIFI_STATUS_FAILED&&!transport_connected())app_set_state(UI_STATE_ERROR,"Wi-Fi failed; use settings");
}
void app_main(void){
    ESP_ERROR_CHECK(nvs_flash_init());
    ui_init();
    bsp_battery_init();
    app_init();
    ESP_ERROR_CHECK(bsp_button_init(app_button,NULL));
    ESP_ERROR_CHECK(audio_stream_init());
    ESP_ERROR_CHECK(wifi_portal_init(wifi_status));
    ESP_LOGI("memory","before BLE heap=%u largest=%u",(unsigned)esp_get_free_heap_size(),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    ESP_ERROR_CHECK(ble_link_init());
    transport_init();
    extern void usb_diagnostics_start(void);
    usb_diagnostics_start();
    ESP_LOGI("talk2gemi","2.0 development started; UP/DOWN volume, hold UP status, hold DOWN settings");
}

#pragma once

#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WIFI_STATUS_IDLE = 0,
    WIFI_STATUS_CONNECTING,
    WIFI_STATUS_CONNECTED,
    WIFI_STATUS_AP_MODE,
    WIFI_STATUS_FAILED
} wifi_portal_status_t;

typedef void (*wifi_status_cb_t)(wifi_portal_status_t status, const char *msg);

// 初始化 NVS 并准备 Wi-Fi 栈
esp_err_t wifi_portal_init(wifi_status_cb_t cb);

// 检查是否已有持久化的配置
bool wifi_portal_has_config(void);
esp_err_t wifi_portal_forget_sta(void);

// 连接已有配置的 Wi-Fi (超时单位为秒)
esp_err_t wifi_portal_connect_sta(int timeout_sec);

// 暂停 Wi-Fi 运行时连接 (保留所有 NVS 凭据配置)
void wifi_portal_stop(void);

// 启动 SoftAP + Web 配网服务器
esp_err_t wifi_portal_start_ap_web(void);

// 获取已配置的参数
const char* wifi_portal_get_ssid(void);
const char* wifi_portal_get_server_ip(void);
int wifi_portal_get_server_port(void);
const char* wifi_portal_get_sta_ip(void);
esp_err_t wifi_portal_set_server(const char *host,int port);
bool wifi_portal_is_setup(void);

#ifdef __cplusplus
}
#endif

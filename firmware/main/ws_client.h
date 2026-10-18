#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// 启动 WebSocket 客户端连接至 Mac 中继服务
esp_err_t ws_client_start(const char *server_ip, int port);

// 发送 PTT 按键控制信令 (press=true: start, press=false: stop)
void ws_client_send_ptt(bool press);

// 发送二进制录音 PCM 数据
void ws_client_send_audio(const void *pcm, size_t len);

// 检查连接状态
bool ws_client_is_connected(void);

#ifdef __cplusplus
}
#endif
bool ws_client_send_json(const char *text);
void ws_client_stop(void);

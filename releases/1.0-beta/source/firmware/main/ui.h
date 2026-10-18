#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    UI_STATE_BOOT = 0,
    UI_STATE_WIFI_CONNECTING,
    UI_STATE_WIFI_SETUP,
    UI_STATE_READY,
    UI_STATE_LISTENING,
    UI_STATE_THINKING,
    UI_STATE_SPEAKING,
    UI_STATE_ERROR
} ui_state_t;

// 初始化显示面板与 LVGL
void ui_init(void);

// 切换全局主状态
void ui_set_state(ui_state_t state, const char *extra_info);

// 更新电量显示 (0..100)
void ui_set_battery(int percent);

// 更新用户/模型字幕
void ui_set_transcript(const char *role, const char *text);

#ifdef __cplusplus
}
#endif

typedef struct {
    bool wifi,ble,relay,cloud,menu,listening,error;
    int battery,level;
    int latency_ms;
    int64_t idle_ms;
    int volume;
    bool volume_visible;
    ui_state_t state;
    bool playing;
    char computer[64];
    char title[32],detail[96],body[512],footer[96];
} ui_snapshot_t;
void ui_render(const ui_snapshot_t *model);

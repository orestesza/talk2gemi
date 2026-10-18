#pragma once
#include <stdbool.h>
#include <stddef.h>
void transport_init(void);
bool transport_connected(void);
int transport_active(void); /* 0 none, 1 Wi-Fi, 2 BLE */
bool transport_send_ptt(bool start);
bool transport_send_audio(const void *pcm,size_t length);
void transport_abort(void);
void transport_reconnect(void);
void transport_stop(void);
bool transport_send_control(const char *json);

bool transport_forget_wifi(void);

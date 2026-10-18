#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
typedef struct {char name[48]; int rssi;} ble_candidate_t;
esp_err_t ble_link_init(void);
void ble_link_scan(void);
int ble_link_candidates(ble_candidate_t *out,int count);
void ble_link_connect(int candidate);
void ble_link_reconnect(void);
void ble_link_forget(void);
bool ble_link_connected(void);
int ble_link_rssi(void);
bool ble_link_send_json(const char *json);
bool ble_link_send_audio(const void *pcm,size_t length);
void ble_link_stop(void);
void ble_link_disconnect(void);
bool ble_link_confirm_pairing(int code);
void ble_link_reject_pairing(void);

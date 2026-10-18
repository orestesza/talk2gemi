#pragma once
#include <stddef.h>
#include <stdint.h>
/* Independent blocks, low nibble first. See server/ble_protocol.py. */
size_t ima_encode(const int16_t *pcm, size_t samples, uint8_t *out, size_t capacity);
size_t ima_decode(const uint8_t *data, size_t length, int16_t *out, size_t capacity);

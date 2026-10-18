#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// 初始化音频硬件 (ES8311 Codec 16kHz 16-bit Mono)
esp_err_t audio_stream_init(void);

// 开始/停止麦克风录音推流 (由 PTT 按键驱动)
void audio_stream_set_recording(bool active);

// 播放下行音频数据
esp_err_t audio_stream_play_chunk(const void *pcm, size_t bytes);

#ifdef __cplusplus
}
#endif
bool audio_stream_playing(void);
// Development diagnostics, emitted by the UI task rather than the audio tasks.
void audio_stream_log_stack_usage(void);
void audio_stream_stop(void);

void audio_stream_flush_playback(void);

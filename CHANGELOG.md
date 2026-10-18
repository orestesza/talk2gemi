# 更新日志 (Changelog)

本项目所有重要更新记录均记录于此文件。
版本格式遵循 [语义化版本 2.0.0 (SemVer)](https://semver.org/lang/zh-CN/)，文档组织遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

---

## [1.0.0-beta] - 2026-10-18

### 新增 (Added)
- **硬件 PTT 语音上行对讲**：
  - 基于 FoloToy AI Passport (ESP32-C3 + ES8311 + ST7789 + 正面 OK 按键) 打造专用 Gemini 3.8 Live 语音终端。
  - 采用 Google 官方 Manual Activity 边界（按下 `activity_start`、16kHz 单声道 PCM 录音以 IMA ADPCM 压缩传输、松开 `activity_end`）。
- **纯 BLE GATT 双向传输链路**：
  - 摆脱设备对局域网 Wi-Fi 的依赖，通过 Mac 蓝牙低功耗 (BLE GATT) 承载全双工实时音频与控制信令。
  - 自定义分包与帧格式：`kind=1` 控制帧、`kind=4` 上行独立 IMA ADPCM 语音块、`kind=2` 下行音频帧、`kind=3` ACK 回执。
  - 下行采用 3 帧有界滑动窗口与设备硬件播放入队回执背压流控，配合最长 80ms 首段预缓冲，消除断音与卡顿。
- **长语音播放队列与消费协程**：
  - 在 `server/relay_server.py` 中引入 `self.playback_queue` 与异步 `_playback_worker`，彻底解耦 WebSocket 下行接收与 BLE 硬件流控背压。
  - 毫秒级排空云端长诗与字幕流，避免底层 WS 1006 超时断流。
  - 优化 `_gemini_keepalive_loop`，在 `self.busy` 或播音未排空时严禁抢占取消播音任务。
  - 实机验收：白居易《卖炭翁》全诗（36.5 秒连续播放、47 个音频块、1,168,320 字节 PCM）完整播毕，状态正常落库为 `completed`。
- **高保真 Opus 音频编解码**：
  - 引入 `libopus` 编解码库（16kHz 20ms VBR，约 24kbps），显著降低 BLE 传输带宽占用并提升下行语音保真度。
- **冰蓝雪鸮动态主界面**：
  - 1.54 英寸 ST7789 240×320 彩色 SPI LCD 视觉重绘，采用纯英文排版与冰蓝/冷灰配色。
  - 具备 6 大运行状态动态表情切片（Ready, Listening, Thinking, Speaking, Resting, Error）。
  - 状态栏实时显示电池电量、连接状态图标、电脑名走马灯滚动及网络延迟。
- **macOS 伴侣应用与标准安装包**：
  - 原生 Swift 编写菜单栏伴侣应用，状态栏展示黑色雪鸮模板图标，自动适配系统浅色与深色外观。
  - 构建标准 `dist/Talk2Gemi.dmg` 磁盘镜像安装包（94MB，UDZO 高压缩格式，内置 `/Applications` 替身链接、雪鸮卷标图标，支持一拖即装）与便携版 ZIP（`dist/talk2gemi-mac-arm64.zip`）。
  - 建立固定本地代码签名身份，API Key 安全保存在 macOS 系统钥匙串中，避免重复授权弹窗。
- **本地会话持久化与 Web 监控台**：
  - 本地 SQLite 数据库记录每轮对话的语音、识别文本与云端回复，支持断线后基于文字历史自动重建上下文。
  - Web 控制台（`http://localhost:8765`）实时查看双语对话字幕流与设备状态。
- **声线热切换弹性恢复**：
  - 切换音色时立即清除旧音色会话恢复句柄，改用本地已完成文字历史重建上下文，后台异步重连，将 BLE `kind=3` ACK 与云端重连解耦，清理残留 `recording` 状态。
- **多卡片固件备份与设备适配工具**：
  - 新增 `scripts/backup_card.py`，支持全自动设备检测、ROM MAC 读取、8MB Flash 全镜像备份与逐分区提取解析。
  - 成功归档第二台设备（Muse 固件，MAC `4C:11:AE:32:35:E4`）的完整备份并提供一键恢复脚本 `backup/muse/restore_muse.sh`。
- **自动化测试套件**：
  - 主机 Python 测试套件共 43 项单元测试，100% 全部通过。

### 安全 (Security)
- 严格审计 DMG 安装包与随附环境，确认 0 API Key、0 密钥外泄，仅在首次使用时通过系统钥匙串安全存储。

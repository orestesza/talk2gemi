# talk2gemi 当前研发状态与工作记录

## 当前版本：1.0 Beta 候选版（2026-10-18）

归档于 `releases/1.0-beta/` 及 `releases/talk2gemi-1.0-beta.zip`。已完成实机验证与功能验收：

1. **彻底修复 >30 秒长语音断流**：
   - 服务端引入异步内存队列 `playback_queue` 与独立的 `_playback_worker`，将云端 WebSocket 接收与 BLE 硬件流控背压彻底解耦。
   - `_gemini_receive_loop` 在数秒内即可接收排空云端推送的音频块与 `turn_complete` 事件，不再阻塞网络套接字。
   - 在 `_gemini_keepalive_loop` 中增加 `not self.busy` 保护，播音期间禁止抢占取消。
   - 实机验证白居易《卖炭翁》全诗连续播报 36.5 秒（1,168,320 字节，47 块音频），状态完整落库 `completed`。

2. **Mac 菜单栏图标升级**：
   - 状态栏图标采用黑色雪鸮模板图标（`owl_status.png` 18x18, `owl_status@2x.png` 36x36），`isTemplate = true`，自动适配 macOS 浅色与深色外观。

3. **标准 macOS DMG 安装包**：
   - 构建标准磁盘镜像 `dist/Talk2Gemi.dmg`（内置 `/Applications` 拖拽替身），并提供便携版 `dist/talk2gemi-mac-arm64.zip`。

4. **双向纯 BLE 传输与 Opus 音频**：
   - 采用自定义分包与 3 帧有界滑动窗口背压流控，解决蓝牙传输卡顿与栈溢出。
   - 采用 `libopus` 16kHz 20ms VBR 编解码（约 24kbps），保持清晰音质同时降低蓝牙带宽压力。

5. **冰蓝雪鸮动态主界面**：
   - 1.54 寸 ST7789 屏幕呈现 6 态动态表情（Ready, Listening, Thinking, Speaking, Resting, Error），包含电量与电脑名滚动。

6. **会话持久化与声线切换**：
   - 本地 SQLite 记录完整对话历史与状态，支持断线后基于文字历史自动重建上下文。
   - 声线热切换时自动平滑异步重连并清理残留录音状态。

7. **多卡片备份工具**：
   - 开发 `scripts/backup_card.py`，完成卡片 8MB 全 Flash 备份与分区提取归档 (`backup/muse/`)。

8. **测试覆盖**：
   - 43 项主机单元测试全部通过。

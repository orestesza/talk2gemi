# talk2gemi 通信协议与硬件规范指南

本文档平实记录 talk2gemi 的硬件接口、蓝牙低功耗 (BLE) 帧协议、音频编解码流转以及流控背压技术细节，供开发者参考与二次开发。

---

## 一、 硬件平台与引脚定义

本项目硬件平台为 **FoloToy AI Passport**（随身工牌名片形态）。核心硬件配置如下：

### 1. 核心控制器与存储
- **芯片**：ESP32-C3-WROOM-02
- **架构**：32 位 RISC-V 单核，最高主频 160 MHz
- **内存**：400 KB 内置 SRAM
- **存储**：8 MB 外置 SPI Flash（Quad SPI, 80MHz）

### 2. 音频芯片 (ES8311)
Everest ES8311 是一颗低功耗单声道音频编解码器，通过 I2C 进行寄存器配置，通过 I2S 传输音频数据。
- **I2C 控制引脚**：
  - `SDA`：GPIO 6
  - `SCL`：GPIO 7
  - `I2C 端口`：I2C_NUM_0，标准速率 100 kHz
- **I2S 音频引脚**：
  - `MCLK`：GPIO 3
  - `BCLK`：GPIO 2
  - `WS (LRCK)`：GPIO 10
  - `DOUT (播放)`：GPIO 4
  - `DIN (录音)`：GPIO 5
- **音频采样率**：标准 16,000 Hz，16-bit，单声道。
- **扬声器功放**：功放使能引脚受 GPIO 8 控制，工作时拉高使能。

### 3. 显示屏 (ST7789)
1.54 英寸彩色 SPI LCD，分辨率 240×320，颜色格式 RGB565。
- `MOSI (SDA)`：GPIO 18
- `SCLK`：GPIO 19
- `CS`：GPIO 1
- `DC (命令/数据)`：GPIO 0
- `RST`：复位引脚
- `BL (背光)`：受 PWM 控制调节亮度

### 4. 物理按键
- **正面 OK 键**：分压按键，通过 GPIO 9（或对应 ADC 通道）读取。本项目用作 Push-To-Talk (PTT) 语音键。
- **侧面 UP 键 / DOWN 键**：用于调节音量及进入系统设置菜单。

---

## 二、 蓝牙低功耗 (BLE GATT) 通信协议

talk2gemi 设备端充当 BLE 外设 (Peripheral)，Mac 伴侣端充当中央设备 (Central)。两端通过自定义 GATT 特征值收发数据。

### 1. 数据帧封装格式 (Packet Framing)

所有收发的数据包均采用统一的二进制头部封装（小端序）：

```text
+--------+--------+--------+--------+------------------+---------------------+
| Byte 0 | Byte 1 | Byte 2 | Byte 3 |  Bytes 4 ~ 5     |  Bytes 6 ~ N        |
| Magic  |  Seq   |  Kind  | Flags  | Payload Length   |      Payload        |
| (0x54) | (0-255)| (类型) | (标志) |  (uint16_le)     |    (变长有效载荷)   |
+--------+--------+--------+--------+------------------+---------------------+
```

- **Magic (魔数)**：固定为 `0x54`（ASCII 字符 `'T'`）。
- **Seq (序号)**：0 ~ 255 循环递增的包序号，用于帧校验与重排。
- **Kind (帧类型)**：
  - `kind = 1`：控制指令帧（如 `start`、`stop`、`hello`、`state`、`codec` 等 JSON 文本）。
  - `kind = 2`：下行音频帧（Opus 或 ADPCM 音频包）。
  - `kind = 3`：传输回执 ACK 帧（确认收到某一序号的包）。
  - `kind = 4`：上行音频帧（设备麦克风录入的 ADPCM 压缩语音块）。
- **Flags**：保留控制位。
- **Payload Length**：有效载荷长度（字节数）。

---

## 三、 上行与下行流畅流控机制

BLE 链路相较于 Wi-Fi 具有带宽有限（最大实际吞吐约 20~50 KB/s）、连接间隔固定（通常 15~30ms）等特点。为保障音频连续且不溢出，系统采用了针对性的分流与流控策略：

### 1. 上行音频流控策略
- **问题背景**：早期版本对麦克风录音块采用与控制帧相同的逐包应用层 ACK 机制。在网络稍有抖动时，上传队列积压超过 FreeRTOS 环形缓冲容量，触发设备主动断连（报错 `Uplink buffer full / Audio link interrupted`）。
- **优化实现**：
  - 上行音频独立分配为 `kind = 4` 独立数据帧；
  - 录音块直接以定长块（1024 字节 PCM 压缩为 256 字节 ADPCM）单向推流，不要求 Mac 逐包回发应用 ACK；
  - 仅在录音开始（`start`）与结束（`stop`）的关键控制帧上强制校验 ACK。
  - 彻底消除了因回执等待造成的上行缓冲溢出。

### 2. 下行音频 3 帧有界滑动窗口与背压机制
- **问题背景**：设备端 ESP32-C3 的音频解码与 I2S 播放受硬件定时器驱动（1 秒只能消化 16,000 个采样点），而 Mac 服务端下发速度可能远高于播放速度，容易撑爆设备端有限的 RAM 缓冲。
- **优化实现**：
  - **有界在途窗口**：Mac 伴侣端维持一个滑动窗口，在途（已发送但未收到设备入队确认）的下行音频帧数严格限制为不超过 **3 帧**。
  - **硬件播放回执**：设备在将音频帧成功放入播放 FIFO 队列后，通过 `kind = 3` 回发携带该包序号的 ACK。Mac 收到确认后才允许滑动窗口并发送下一帧。
  - **首包预缓冲**：下行首段下发时，Mac 预先连续下发最长 80ms 音频数据，让设备端播放缓冲区快速建立水线，避免启动瞬态爆音或欠载。

### 3. 长语音播放队列解耦 (Decoupled Playback Queue)
- **问题背景**：当用户请求长诗背诵或长篇回答时（单轮语音 > 30 秒），Gemini 云端数秒内即可通过 WebSocket 推送完数万字及对应音频，但设备需要 30+ 秒才能通过 BLE 播完。旧版本在 WebSocket 接收循环内直接调用阻塞的 `send_audio_chunk`，导致 WebSocket 底层缓冲区长期得不到读取排空，触发 WebSocket 1006 异常断开。同时，15 秒保活巡检因误判会话无应答而强行重连。
- **优化实现**：
  - 在服务端引入独立的内存播放队列 `self.playback_queue` 与异步消费协程 `_playback_worker`。
  - WebSocket 接收循环（`_gemini_receive_loop`）收到云端音频块、字幕与 `turn_complete` 后，立即推入队列，毫秒级排空网络套接字。
  - 由 `_playback_worker` 独立按照 BLE 3 帧滑动窗口节奏向设备下发音频。
  - 在 `_gemini_keepalive_loop` 中加入 `if not self.busy:` 判定，播音未排空时严禁抢占取消任务。

---

## 四、 音频编解码流水线

```text
[录音上行链路]
ES8311 麦克风 -> 16kHz 16-bit PCM (1024字节/32ms) -> IMA ADPCM 编码 (256字节)
               -> BLE kind=4 数据帧 -> Mac 伴侣端 -> ADPCM 解码为 16kHz PCM
               -> 推流给 Google Gemini Live API

[播音下行链路]
Gemini 3.8 Live API -> 24kHz 单声道 PCM 音频流
                    -> Mac 服务端重采样 (audioop) 至 16,000 Hz
                    -> libopus 编码器 (16kHz, 20ms 帧长, VBR ~24kbps)
                    -> 推入 playback_queue
                    -> _playback_worker 配合 3 帧流控经 BLE kind=2 下发
                    -> 设备端 libopus 解码器 -> 16kHz 16-bit PCM
                    -> I2S DMA -> ES8311 芯片 -> 单声道喇叭
```

---

## 五、 固件分区与烧录救砖指南

### 1. Flash 分区表布局 (8MB)

```text
# Name,   Type, SubType, Offset,   Size,     Flags
nvs,      data, nvs,     0x9000,   0x6000,
phy_init, data, phy,     0xf000,   0x1000,
factory,  app,  factory, 0x10000,  0x300000,
storage,  data, fat,     0x310000, 0x4f0000,
```

- `0x0000`：二级引导程序（Bootloader）
- `0x8000`：分区表（Partition Table）
- `0x9000`：NVS 键值存储（保存配对信息、系统设置）
- `0x10000`：应用主固件（Factory App，约 2.1 MB）

### 2. 安全烧录操作规范
为防止意外破坏出厂配对或引导分区，推荐使用项目专用的烧录工具：
```bash
python3 talk2gemi/scripts/flash_candidate.py --port /dev/cu.usbmodem1101 --write
```
该脚本在烧录前会自动核对设备 USB VID:PID（`303a:1001`）、ROM MAC 地址以及 `0x8000` 分区表哈希，且**只对 `0x10000` 应用分区进行写入**，严禁全片擦除。

### 3. 出厂固件恢复（救砖）
如需彻底还原为原厂固件（如 Muse 或 Trae 名片系统）：
```bash
bash talk2gemi/backup/muse/restore_muse.sh /dev/cu.usbmodem1101
```
脚本会将备份目录中原汁原味的 8MB 原始镜像逐字节刷回设备。

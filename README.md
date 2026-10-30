# Refrigerate_System
**_Please ensure your personal safety!!!_ and then ensure compressor safety**>_<

The open source refrigeration (heat pump) system

In this project, I will use STM32 + Raspberry PICO2(for motor driver) + Micropython to design. Because I am not an all-round talent. Some work will be done by AI. Thanks to Ms.Claude, ChatGPT, Kimi & Deepseek.

Mainflow:
```mermaid
flowchart TD
    PWR(["上电 / 复位"]) --> IMP["import 期硬件初始化<br/>SPI1 / UART1/6/7/8 / ADC / TMC2209"]
    IMP --> MAIN["main()<br/>load_config + EXV PID 注入"]
    MAIN --> GATHER["asyncio.gather<br/>11 个协程"]
    GATHER --> T1["supervisor tick #1<br/>BOOT → SELF_TEST"]
    T1 --> T2["supervisor tick #2（约 100ms 后）<br/>SELF_TEST → SAFE_STOP"]
    T2 --> WAIT{"ENABLE = 1 ?"}
    WAIT -- 否 --> WAIT
    WAIT -- 是 --> GUARD["启动准入检查<br/>见 §5"]
    GUARD -- 拒绝 --> WAIT
    GUARD -- 通过 --> STARTUP["STARTUP<br/>S0 → S6"]
    STARTUP -- "S1-S5 失败/超时" --> SAFE["SAFE_STOP"]
    STARTUP -- S6 --> RUN["RUNNING<br/>三环控制 + 100ms 保护"]
    RUN <--> ALARM["ALARM<br/>可恢复报警，继续运行"]
    RUN -- "ENABLE=0 / 正常停机规则" --> STOP["STOPPING<br/>N1-N5"]
    RUN -- "保护延时到点" --> FSTOP["STOPPING<br/>E0-E4"]
    ALARM -- "保护延时到点" --> FSTOP
    STOP --> SAFE
    FSTOP --> SAFE
    SAFE -- "手动复位 / 再次启动" --> WAIT
```
All Process Mainflow:

```mermaid
flowchart TD
    PWR(["上电 / 复位"]) --> IMP["import 期初始化<br/>SPI1 / UART1/6/7/8 / ADC / TMC2209"]
    IMP --> MAIN["main()<br/>load_config + EXV PID 注入"]
    MAIN --> GATHER["gather 11 协程"]
    GATHER --> T1["tick#1<br/>BOOT → SELF_TEST"]
    T1 --> T2["tick#2（+100ms）<br/>SELF_TEST → SAFE_STOP"]
    T2 --> SAFE["SAFE_STOP<br/>ESC0-3 disable / 联锁=0<br/>Sensor / Modbus / Display 仍在跑"]
    SAFE --> WAIT{"coils[0]=1 ?"}
    WAIT -- 否 --> SAFE
    WAIT -- 是 --> G1["① ENABLE=1"]
    G1 --> G2["② retry_lockout=0"]
    G2 --> G3["③ 阈值单调性"]
    G3 --> G4["④ 无严重锁存故障"]
    G4 --> G5["⑤ 链路检查"]
    G5 --> G6["⑥ 启动次数未超"]
    G6 -- 通过 --> S0["S0 锁存启动"]
    G2 -- 拒绝 --> SAFE
    G3 -- 失败 --> CFG["CONFIG_IO_FAULT<br/>1x11 / bit10"] --> SAFE
    G4 -- 拒绝 --> SAFE
    G5 -- 拒绝 --> SAFE
    G6 -- 拒绝 --> SAFE
    S0 --> S1["S1 冷凝风机<br/>4x122=1000"]
    S1 -->|"ESC2 rpm 3x215 超过 100"| S2["S2 蒸发风机<br/>4x123=800"]
    S1 -->|"10s 超时/故障"| F1["COND_FAN_FAULT<br/>1x4"] --> SAFE
    S2 -->|"ESC1 rpm 3x211 超过 100"| S3["S3 EXV<br/>4x120=30.0%"]
    S2 -->|"10s 超时/故障"| F2["EVAP_FAN_FAULT<br/>1x3"] --> SAFE
    S3 -->|"3x12 与 30% 误差 ≤2%"| S4["S4 等 4x101=5s<br/>压缩机 4x121=1200"]
    S3 -->|"10s 超时/故障"| F3["EXV_FAULT<br/>1x1"] --> SAFE
    S4 -->|"3x201 超过 100"| S5["S5 晋级<br/>coil 0x8"]
    S4 -->|"20s 超时/故障"| F4["COMPRESSOR_FAULT<br/>1x2"] --> SAFE
    S5 -->|"模式0 定速10s<br/>模式1 15s+10s窗口"| S6["S6 RUNNING<br/>R1 / 3x100=4"]
    S5 -->|"30s 超时"| F5["COMPRESSOR_FAULT<br/>1x2"] --> SAFE
    S6 --> RUN["RUNNING<br/>容量环10s / 冷凝2s / 过热度1s / 转速0.5s<br/>R1⇄R2⇄R4，L1→R3"]
    RUN -->|"ENABLE=0 / 60s 无有效 Modbus 帧"| STOP["STOPPING N1-N5<br/>5s 停压缩机 / 再5s 停风机"]
    RUN -->|"保护延时到点"| FSTOP["STOPPING E0-E4<br/>压缩机立即停 / 高压风机全速 / 15s后全停"]
    RUN -->|"error_reg 出现 0 位"| ALARM["ALARM 5<br/>可恢复报警，继续运行"]
    ALARM -->|"error_reg=0xFFFFFFFF"| RUN
    ALARM -->|"保护延时到点"| FSTOP
    STOP --> SAFE
    FSTOP --> SAFE
    SAFE -->|"故障锁存"| RESET{"coil 0x7 复位?"}
    RESET -- "SAFE_STOP/DEGRADED 受理" --> CLR["清可复位位 + 清计时器"]
    CLR --> WAIT
```



**All codes & Refrigeration designs will be open source!**

**Let everyone make the air conditioner they want.** I believe that open source can lead to progress.

The code is being updated

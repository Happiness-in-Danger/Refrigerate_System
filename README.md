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

**All codes & Refrigeration designs will be open source!**

**Let everyone make the air conditioner they want.** I believe that open source can lead to progress.

The code is being updated

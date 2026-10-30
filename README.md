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
flowchart TB
    subgraph GA["A 上电与初始化"]
        direction TB
        A1["A1 Board/board.py<br/>触发: MCU 复位/上电<br/>动作: SPI1 500k(CS PG15)、UART1、UART8<br/>ADC PC2/PC3、PA4/PA5、流量 DI<br/>点位: UART1/UART8/SPI1/ADC<br/>下一步: A2<br/>失败: 资源初始化异常 → import 失败"]
        A2["A2 EXV/EXV.py<br/>触发: A1 完成<br/>动作: TMC2209 UART7 115200<br/>PD13 STEP / PE10 DIR / PE12 EN<br/>等待 100ms 后写驱动寄存器<br/>点位: UART7/PD13/PE10/PE12<br/>下一步: A3<br/>失败: import 失败（B5）"]
        A3["A3 state/PID/Modbus<br/>触发: A2 完成<br/>动作: sys_mode=BOOT(0)<br/>error_reg=0xFFFFFFFF<br/>sensor_data=999、quality=BAD<br/>PID 实例、Driver 惰性、四区全 0<br/>点位: sensor_fault_reg=0xFFFF<br/>下一步: A4<br/>失败: 默认值兜底"]
        A4["A4 main()<br/>触发: import 完成<br/>动作: load_config 校验 version/checksum<br/>apply_params；创建 EXV PID<br/>set_controller；gather(11)<br/>点位: config.json / 4x1-4x7<br/>下一步: B1<br/>失败: 配置失败用默认值"]
        A1 --> A2 --> A3 --> A4
    end

    subgraph GB["B 自动进入待机"]
        direction TB
        B1["B1 tick#1<br/>触发: supervisor 首 tick<br/>动作: 无设备动作<br/>状态: BOOT(0) → SELF_TEST(1)<br/>点位: 3x100=1<br/>下一步: B2<br/>失败: 无"]
        B2["B2 tick#2<br/>触发: B1 后约 100ms<br/>动作: SELF_TEST → SAFE_STOP<br/>_enter_safe_stop 全输出归零<br/>stage=0 / substate=3 / base=0<br/>点位: 3x100=2、3x101=0、3x102=3<br/>下一步: B3<br/>失败: 无"]
        B3["B3 SAFE_STOP 稳态<br/>触发: B2 完成<br/>动作: Sensor/Modbus/Display/params 继续<br/>supervisor 每 100ms 查 ENABLE<br/>点位: 0x1 / 3x100=2<br/>下一步: 0x1=1 → C1<br/>失败: 无；不会自动运行"]
        B1 --> B2 --> B3
    end

    subgraph GC["C 启动准入（6 道门）"]
        direction TB
        C1["C1 ① ENABLE<br/>触发: B3 且 coils[0]=1<br/>动作: 读 ST_SYSTEM_ENABLE<br/>点位: 0x1<br/>下一步: C2<br/>失败: 0 → 留 B3"]
        C2["C2 ② 重试锁定<br/>触发: C1 通过<br/>动作: 查 retry_lockout<br/>点位: 1x105<br/>下一步: C3<br/>失败: 置位 → 留 B3"]
        C3["C3 ③ 阈值单调<br/>触发: C2 通过<br/>动作: HP/I/TD 为 warn 小于 unload 小于 trip<br/>LP 为 MIN 小于 UNLOAD 小于 WARN<br/>点位: 4x31-4x34、4x50-4x62<br/>下一步: C4<br/>失败: CONFIG_IO_FAULT（1x11/bit10）"]
        C4["C4 ④ 严重故障<br/>触发: C3 通过<br/>动作: 检查未复位严重位<br/>点位: 1x1-1x16 中的严重位<br/>下一步: C5<br/>失败: 存在 → 留 B3"]
        C5["C5 ⑤ 链路检查<br/>触发: C4 通过<br/>动作: LINK_CHECK_ENABLE=1 时查 link<br/>点位: 1x100 / LINK_CHECK_ENABLE<br/>下一步: C6<br/>失败: 断链 → 留 B3；默认跳过"]
        C6["C6 ⑥ 启动次数<br/>触发: C5 通过<br/>动作: 清理 1 小时外记录并比较上限<br/>点位: 4x202<br/>下一步: D0<br/>失败: 超限 → 留 B3"]
        C1 --> C2 --> C3 --> C4 --> C5 --> C6
    end

    subgraph GD["D STARTUP S0-S6"]
        direction TB
        D0["D0 S0 锁存<br/>触发: C6 通过<br/>动作: sys_mode=STARTUP(3)<br/>startup_stage=1<br/>点位: 3x100=3、3x101=1<br/>下一步: D1<br/>失败: 无"]
        D1["D1 S1 冷凝风机<br/>触发: D0<br/>动作: cond_fan_enable=True / 4x122=1000<br/>判据: ST_COND_FAN_FAULT=0 且 3x215 超过 100<br/>点位: 4x122 / 3x215 / 1x4<br/>下一步: D2<br/>失败: 10s → COND_FAN_FAULT"]
        D2["D2 S2 蒸发风机<br/>触发: D1<br/>动作: evap_fan_enable=True / 4x123=800<br/>判据: ST_EVAP_FAN_FAULT=0 且 3x211 超过 100<br/>点位: 4x123 / 3x211 / 1x3<br/>下一步: D3<br/>失败: 10s → EVAP_FAN_FAULT"]
        D3["D3 S3 EXV 定位<br/>触发: D2<br/>动作: exv_enable=True / 4x120=30.0%<br/>首次 enable 先 homing 约 3.5s<br/>判据: ST_VALVE_FAULT=0 且 3x12 误差 ≤2%<br/>点位: 4x120 / 3x12 / 3x13 / 1x1<br/>下一步: D4<br/>失败: 10s → EXV_FAULT（见 §5.2）"]
        D4["D4 S4 压缩机软启<br/>触发: D3<br/>动作: 4x130 槽号取 4x101=5s<br/>comp_enable=True / 4x121=1200<br/>判据: 3x201 超过 100<br/>点位: 4x130 / 4x101 / 4x121 / 3x201 / 1x2<br/>下一步: D5 或 D6<br/>失败: 20s（含 5s）→ COMPRESSOR_FAULT"]
        D5["D5 S5 模式0 定速<br/>触发: D4 且 coil 0x8=0<br/>动作: 保持启动油门<br/>等待 ESC0 反馈连续有效<br/>判据: 连续 10s（COMP_HOLD_S）<br/>点位: 0x8 / 3x201<br/>下一步: D7<br/>失败: 30s → COMPRESSOR_FAULT"]
        D6["D6 S5 模式1 稳定性<br/>触发: D4 且 coil 0x8=1<br/>动作: 4x150=15s 延时<br/>4x151=10s 窗口 / 每 1s 采样<br/>判据: 窗口内 max-min ≤ 4x152=100rpm<br/>点位: 0x8 / 4x150-4x152 / 3x201<br/>下一步: D7<br/>失败: 30s → COMPRESSOR_FAULT"]
        D7["D7 S6 RUNNING<br/>触发: D5 或 D6 成功<br/>动作: sys_mode=RUNNING(4)<br/>substate R1 / base 1200 和 1000<br/>启动次数 +1<br/>点位: 3x100=4、3x101=6、3x102=0<br/>下一步: E0<br/>失败: 无"]
        D0 --> D1 --> D2 --> D3 --> D4
        D4 --> D5 --> D7
        D4 --> D6 --> D7
    end

    subgraph GE["E RUNNING 三环与子状态"]
        direction TB
        E0["E0 R1 PULLDOWN<br/>触发: D7<br/>动作: 外环投运<br/>supervisor 每 100ms 复制基值到 interlocks<br/>点位: 1x101-1x104 / 3x200-3x205<br/>下一步: E6/E7/E8<br/>失败: 无"]
        E1["E1 容量环<br/>触发: RUNNING 且非手动/非 R4<br/>周期: 4x24 默认 10s<br/>动作: PID 3x4 vs 4x19<br/>输出 comp_throttle_base / 限幅 4x21、4x22<br/>点位: 3x4 / 4x19 / 3x200 / 3x201<br/>失败: 质量坏或 age 超过 2000ms → 冻结"]
        E2["E2 冷凝压力环<br/>触发: RUNNING 且非手动/非 R4<br/>周期: 2s（内部参数）<br/>动作: PID 3x6 vs 4x34<br/>输出 cond_fan_throttle_base / 限幅 4x28、4x29<br/>L1 ACT_COND_FAN_MAX → 4x29<br/>点位: 3x6 / 4x34 / 3x204 / 3x205<br/>失败: 质量坏或 age 超过 2000ms → 冻结"]
        E3["E3 过热度环<br/>触发: RUNNING 且非手动<br/>周期: 1s<br/>动作: PID 3x8 vs 4x4<br/>输出 EXV 增量 / 限幅 4x7<br/>质量门禁 3400ms<br/>点位: 3x8 / 4x4 / 4x7 / 3x12 / 3x13<br/>失败: 质量坏/超龄 → 保持开度"]
        E4["E4 转速环<br/>触发: 每 0.5s<br/>使能: coil 0x3/0x4/0x5<br/>动作: 基础油门→目标 rpm→PID 修正<br/>反馈: 3x207/3x211/3x215<br/>点位: 3x201/3x203/3x205<br/>失败: 反馈 999 跳过 / 禁用开环"]
        E5["E5 蒸发风机<br/>触发: RUNNING 且使能<br/>动作: 始终用 4x222 用户油门<br/>映射 ESC1 / 镜像 3x202、3x203<br/>判据: 反馈有效且偏差 ≤ FAN_RPM_TOL<br/>点位: 4x222 / 3x202 / 3x203 / 1x3<br/>失败: ST_EVAP_FAN_FAULT 事件 → 规则延时"]
        E6["E6 R1 与 R2<br/>触发: RUNNING<br/>判据: 3x4 与 4x19 偏差 ≤2K → R2<br/>R2 偏差超过 2K → R1<br/>点位: 3x4 / 4x19<br/>下一步: E0<br/>失败: 无超时"]
        E7["E7 R3 LIMITED<br/>触发: 任一 L1 动作<br/>判据: limit_actions 含 0x01/0x02<br/>动作: 容量环禁升/降容<br/>点位: 3x223 / 4x185 / 4x186<br/>下一步: 限载解除 → R1<br/>失败: 条件持续保持 R3"]
        E8["E8 R4 THERMO_OFF<br/>触发: R1/R2 且 rpm≤下限<br/>且 3x4≤4x19 且 MIN_ON 满足<br/>动作: 压缩机停 / EXV 10% / 冷凝风机停<br/>蒸发风机保持 / comp、cond base=0<br/>点位: 4x200=180s / 4x201=300s<br/>下一步: 回差+MIN_OFF → R1<br/>失败: 无超时；R4 不更新外环"]
        E9["E9 ALARM<br/>触发: error_reg != 0xFFFFFFFF<br/>动作: 继续运行<br/>不刷新外环基值，保持最后联锁<br/>点位: 1x1-1x16 / 3x110 / 3x111<br/>下一步: 全部恢复 → RUNNING/R1<br/>失败: 锁存位需复位；持续报警可停机"]
        E10["E10 手动模式<br/>触发: coil 0x2=1 且 RUNNING<br/>动作: 4x220/4x221/4x222/4x223 覆盖<br/>L1 COND_FAN_MAX 仍覆盖冷凝风机<br/>点位: 0x2 / 4x220-4x223 / 3x223<br/>下一步: E0<br/>失败: 非 RUNNING 不生效"]
        E0 --> E1
        E0 --> E2
        E0 --> E3
        E0 --> E4
        E0 --> E5
        E0 --> E6
        E0 --> E7
        E0 --> E8
        E0 --> E9
        E0 --> E10
        E6 -.->|"R2→R1"| E0
        E7 -.->|"限载解除"| E0
        E8 -.->|"MIN_OFF"| E0
        E9 -.->|"恢复"| E0
    end

    subgraph GF["F 正常停机 N1-N5"]
        direction TB
        F1["F1 N0/N1 入口<br/>触发: ENABLE 1→0 或 ACT_NORMAL_STOP<br/>MODBUS_COMM_FAULT 60s 无帧<br/>动作: STOPPING(6) / exv_target=0<br/>comp、cond base=0 / substate=R4<br/>点位: 3x100=6 / 3x102=3 / 3x13<br/>下一步: F2<br/>失败: N1 关阀受 ENABLE 门控（§5.1）"]
        F2["F2 N2 停压缩机<br/>触发: 4x131 槽号取 4x101=5s<br/>动作: comp disable / throttle=0<br/>点位: 4x131 / 4x101 / 1x101<br/>下一步: F3<br/>失败: 无"]
        F3["F3 N3 停风机<br/>触发: 4x132 槽号取 4x101=5s<br/>动作: 蒸发/冷凝风机停<br/>点位: 4x132 / 4x101 / 1x102 / 1x103<br/>下一步: F4<br/>失败: 无"]
        F4["F4 N5 收尾<br/>触发: N3 完成<br/>动作: _enter_safe_stop 全输出归零<br/>有 dirty 立即落盘<br/>点位: 3x100=2 / 1x101-1x104=0<br/>下一步: SAFE_STOP<br/>失败: 无"]
        F1 --> F2 --> F3 --> F4
    end

    subgraph GG["G 故障停机 E0-E4"]
        direction TB
        G1["G1 E0 入口<br/>触发: 规则延时到点 + ACT_SAFE_STOP<br/>动作: STOPPING / 锁存 error_reg<br/>压缩机立即停 / EXV=0<br/>高压→冷凝风机 2047 / 链路断→全 ESC 停<br/>点位: error bit / 3x223 / 1x101-1x104<br/>下一步: G2<br/>失败: 故障锁存，禁止自动重启"]
        G2["G2 E1 压缩机<br/>触发: E0<br/>动作: throttle=0 + disable<br/>点位: 1x101 / 3x201<br/>下一步: G3<br/>失败: 无 5s 等待"]
        G3["G3 E2/E3 EXV 与高压<br/>触发: E1<br/>动作: EXV=0；高压→冷凝风机全速<br/>链路断→全 ESC 停；否则风机保持<br/>点位: 3x13 / 1x4 / 1x103<br/>下一步: G4<br/>失败: 无"]
        G4["G4 E4 收尾<br/>触发: E0 后 15s（FAULT_FAN_HOLD_MS）<br/>动作: 风机停 + _enter_safe_stop<br/>点位: 3x100=2<br/>下一步: SAFE_STOP<br/>失败: 故障锁存，等 coil 0x7"]
        G1 --> G2 --> G3 --> G4
    end

    subgraph GH["H 复位与再启动"]
        direction TB
        SAFE["SAFE_STOP<br/>全输出归零"]
        H1["H1 复位请求<br/>触发: 写 coil 0x7=1<br/>动作: coils[6]=1 → fault_reset_request<br/>回读自动清 0<br/>点位: 0x7 / coils[6]<br/>下一步: H2<br/>失败: 写 0 无效；非脉冲不保持"]
        H2["H2 复位受理<br/>触发: supervisor 下一 tick<br/>动作: 仅 SAFE_STOP/DEGRADED 受理<br/>清可复位位 / 驱动锁存 / 计时器<br/>点位: 1x1-1x16 / 1x100<br/>下一步: H3<br/>失败: RUNNING/STARTUP/STOPPING 拒绝"]
        H3["H3 复位后是否重启<br/>触发: H2 受理后<br/>动作: 重新走 C1-C6<br/>点位: 0x1 / 1x1-1x16 / 4x202<br/>下一步: ENABLE=1 且准入→D0<br/>失败: 否则留 B3；正常停机无需复位"]
        SAFE --> H1 --> H2 --> H3
    end

    subgraph GI["I 常驻任务（与主链路并行）"]
        direction TB
        I1["I1 Sensor<br/>周期: 200ms（墙上 760-850ms）<br/>动作: PT100/压力/电流/SH/E/SC<br/>validate + update_all_sensor_faults<br/>点位: 3x1-3x17<br/>失败: AD7124 失败 → 温度 999"]
        I2["I2 Modbus 从机<br/>触发: UART8 收帧<br/>动作: FC01/02/03/04/05/06/0F/10<br/>写 coil/HR 联动 state + mark_dirty<br/>点位: 0x1-0x8 / 1x / 3x / 4x<br/>失败: CRC/地址/范围错 → 异常码"]
        I3["I3 Modbus 镜像<br/>周期: 200ms<br/>动作: coils/IR/HR 从 state 回填<br/>点位: 3x1-3x223 / 1x1-1x105 / 4x1-4x223<br/>失败: 无"]
        I4["I4 Display<br/>周期: 1s<br/>动作: UART1 A5 5A 测试屏帧<br/>点位: UART1 / sensor_data<br/>失败: 写失败不影响控制"]
        I5["I5 DriverLink<br/>周期: 5ms 轮询 / 10ms 发命令<br/>动作: 收 0x5A/CRC8/遥测换算<br/>发 0xA5 4 路命令 / 100ms 无帧判断<br/>点位: UART6 / 3x207-3x222 / 1x100<br/>失败: 初始化失败退出（§5.4）"]
        I6["I6 params_store<br/>周期: 1s tick / 30s 防抖<br/>动作: dirty 30s 落盘 / SAFE_STOP 立即落盘<br/>原子写 tmp+rename<br/>点位: config.json<br/>失败: 写失败打印，不阻塞"]
        I7["I7 supervisor<br/>周期: 100ms<br/>动作: 22 条规则 / 状态机 / 复位 / 联锁 / 子状态<br/>点位: error_reg / 3x100-3x102 / 3x223<br/>失败: tick 异常 → SAFE_STOP"]
    end

    A4 --> B1
    B3 --> C1
    C6 --> D0
    D7 --> E0
    E0 -->|"ENABLE=0 / ACT_NORMAL_STOP"| F1
    E0 -->|"ACT_SAFE_STOP"| G1
    E9 -->|"ACT_SAFE_STOP"| G1
    F4 --> SAFE
    G4 --> SAFE
    H3 -->|"ENABLE=1 且准入通过"| C1
    H3 -->|"否则"| B3

    classDef boot fill:#e3f2fd,stroke:#1565c0,color:#000;
    classDef main fill:#e8eaf6,stroke:#283593,color:#000;
    classDef guard fill:#e0f2f1,stroke:#00695c,color:#000;
    classDef start fill:#fff3e0,stroke:#ef6c00,color:#000;
    classDef run fill:#e8f5e9,stroke:#2e7d32,color:#000;
    classDef stop fill:#ffebee,stroke:#c62828,color:#000;
    classDef mode fill:#f3e5f5,stroke:#6a1b9a,color:#000;
    classDef task fill:#fffde7,stroke:#f9a825,color:#000;

    class A1,A2,A3,A4 boot;
    class B1,B2,B3 main;
    class C1,C2,C3,C4,C5,C6 guard;
    class D0,D1,D2,D3,D4,D5,D6,D7 start;
    class E0,E1,E2,E3,E4,E5,E6,E7,E8,E9,E10 run;
    class F1,F2,F3,F4,G1,G2,G3,G4 stop;
    class H1,H2,H3,SAFE mode;
    class I1,I2,I3,I4,I5,I6,I7 task;
```



**All codes & Refrigeration designs will be open source!**

**Let everyone make the air conditioner they want.** I believe that open source can lead to progress.

The code is being updated

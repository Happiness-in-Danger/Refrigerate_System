# 项目文件结构

**用途**：项目目录、模块职责、数据面定义的唯一速查表。
**同步要求**：新增/删除文件、改动 `state.py` 索引时更新本文档。
**最后核对**：2026-11-05（补充物理 I/O 映射和文档导航；state_data 34 项、32 位 error_reg）

- 计划与进度 → `PLAN.md`
- 阶段验收报告 → `REPORT.md`
- 统一设计说明 → `DESIGN.md`
- 开发约束 → `AI_DEVELOPMENT_RULES.md`

---

## 1. 目录树

```
Refrigerate_System/
├── main.py                        192  入口：协程编排 + 依赖注入
├── config.json                         Step6 参数持久化文件（params_store 加载/落盘；state.py 内旧加载逻辑已注释）
├── __init__.py                      0
│
├── Board/                              硬件资源 + 全局数据面
│   ├── board.py                    67  哑资源（UART/SPI/ADC/GPIO）+ REGS
│   ├── state.py                   581  全局数据面唯一定义（参数单源）
│   └── params_store.py             174  Step6 参数持久化（config.json/checksum/防抖）
│
├── Sensor/                             采集与校验
│   ├── read_sensors.py            142  采集协程 reads() / reads_async()
│   ├── validate.py                152  检查层（3 级质量码 Q_OK/Q_SUSPECT/Q_BAD）
│   ├── AD7124_PT100.py            275  PT100 芯片驱动（含 async 方法）
│   ├── ADC_read.py                 73  压力/电流换算
│   └── tem_lastV02_STM32.py        50  死代码（MAX31865 旧方案，Step7 标记保留）
│
├── Alarm/                              保护与状态机
│   ├── supervisor.py              557  sys_mode 状态机 / error_reg 唯一写者
│   └── rules.py                   305  11 条保护规则纯函数表
│
├── EXV/                                电子膨胀阀
│   ├── Valve_CRTL.py              210  过热度 PID + 阀门调度
│   ├── EXV.py                     263  阀门移动 API；B5：import 期建 `_motor`（Step7 保留）
│   └── TMC2209.py                 317  步进驱动器 UART 通信
│
├── HAL/                                硬件抽象 + 纯算法
│   ├── PID_Plus.py                342  增量式 PID（禁止重写）
│   ├── Driver.py                  241  Pico ESC 链路协议
│   ├── SPI.py                      37
│   ├── ADC_sample.py               13  SmoothedADC 滑动均值
│   └── PWM.py                       6  死代码（Step7 标记保留）
│
├── Display_Touch/                      人机通道
│   ├── modbus_slave.py            520  Modbus RTU 从机 + 四区寄存器映射/白名单校验（2026-09-19 重构）
│   ├── modbus_regs.py              71  寄存器地图（coils8/di116/hr223/ir223 四区模型）
│   └── Display.py                  58  A5 5A 测试屏（Step7 由 Desplay.py 更名，A8 已修）
│
├── Extensions/
│   ├── read_r134a.py               45  R134a 物性查表
│   └── pt_table.bin                    物性数据表
│
├── Compressor/
│   ├── __init__.py                  0  公共接口：导出 Compressor()
│   └── control.py                   -  Step5a 软启/调速 + 5b 容量环
├── Evaporator/
│   ├── __init__.py                  0  公共接口：导出 Evaporator()
│   └── control.py                   -  Step5a 风机执行 + 5b 定速策略
├── Condenser/
│   ├── __init__.py                  0  公共接口：导出 Condenser()
│   └── control.py                   -  Step5a 风机执行 + 5b 冷凝压力环
│
├── test/
│   ├── test_step2_verification.py 404  25 用例
│   ├── test_step3_verification.py 907  30 用例
│   ├── test_step4_verification.py 336  23 用例
│   ├── test_step5a_verification.py 347  27 用例
│   ├── test_step5b_verification.py 558  56 用例
│   ├── test_step6_verification.py 560  96 用例
│   ├── test_step7_verification.py 506  72 用例（离线字节级协议 + 更名/A8）
│   └── test_step7_pymodbus_host.py     353  Step7 上板 Modbus 总测（pymodbus 主机脚本）
│
├── docs/                               见 §5
└── Refer-Documents/                    芯片/平台 PDF 手册
```

代码合计约 6100 行（不含 test 4029 行，2026-09-17 实测）。

---

## 2. 模块职责与状态

**完成度分级**：T0 空壳 / T1 代码已写从未运行 / T2 已接线未充分验证 / T3 目标机稳定运行

| 文件 | 级 | 职责 | 修改约束 |
|---|---|---|---|
| `main.py` | T2 | 8 协程 gather 编排、PID 注入、Driver 惰性初始化 | 只改编排，业务逻辑不进 main |
| `Board/board.py` | T3 | 哑资源；**不建带初始化协议的芯片实例**（A11 已修） | REGS 应移交 Display_Touch（历史遗留，Step7 仅标记未迁移） |
| `Board/state.py` | T3 | 全局数据面唯一定义（参数单源） | 改寄存器变量必须 `global` |
| `Board/params_store.py` | T2 | Step6 参数持久化：config.json/checksum/原子写/30s 防抖 | 只读写 state 参数字典，不写 error_reg |
| `Sensor/read_sensors.py` | T2 | 采集；`finally` 保证 validate 必调 | 不删 finally，不建新协程 |
| `Sensor/validate.py` | T2 | 3 级质量检查（Q_OK/Q_SUSPECT/Q_BAD），只判定不动作 | 不写 error_reg，不执行保护动作 |
| `Sensor/AD7124_PT100.py` | T2 | 5×PT100；同步+async 双版本 | async 体内禁用 `time.sleep_ms` |
| `Sensor/ADC_read.py` | T2 | `psi_trans` 走 adc_direct 16-bit；`read_current(bits=)` 走 adc_bits 注册表 | 两条路径位宽语义不同，勿混用 |
| `Alarm/supervisor.py` | T2 | 状态机 / 启停时序 / 延时表 / 复位协议 / **error_reg 唯一写者** | PC/mock 已验证；目标机实测待完成；唯一允许调 set_fault/clear_fault 的模块 |
| `Alarm/rules.py` | T2 | L0-L2 规则与 L1 限载规则（条件+解除+延时+动作+锁存） | 纯条件评估，不产生副作用 |
| `EXV/Valve_CRTL.py` | T2 | 过热度 PID(1s) + 阀门执行(100ms) + 质量门禁 | 不删质量门禁；只写 ST_VALVE_FAULT 事件位 |
| `EXV/EXV.py` | T1 | 阀门移动 API；B5：import 期建 `_motor` | 同上，不得写 error_reg |
| `EXV/TMC2209.py` | T1 | UART7 单线通信 + TIM4 脉冲 | 协议固定，不改 |
| `HAL/PID_Plus.py` | T2 | 增量式 PID 纯算法 | **禁止重写**；MicroPython save/load 兼容已在 Step6 验证 |
| `HAL/Driver.py` | T1 | UART6 921600 CRC8，4×ESC；**Step5a 新增**：`update_telemetry_to_state()` ESC 遥测换算与写入 | 协议与 Pico 强耦合，不改帧格式 |
| `Display_Touch/modbus_slave.py` | T2 | FC 01/02/03/04/05/06/0F/10，标准四区映射 | PC/mock 协议已验证；UART8/RS485 目标机实测待完成 |
| `Display_Touch/Display.py` | T2 | 测试屏，Step7 由 `Desplay.py` 更名（D3），A8 哨兵溢出已修 | 按当前 Modbus 四区点表维护 |
| `Extensions/read_r134a.py` | T2 | 饱和温度 / 焓值查表 | 不改物性数据 |
| `Compressor/` `Evaporator/` `Condenser/` | T2 | 执行层、控制环、RPM 反馈调节与 ESC 命令下发 | 转速环开关独立；手动模式下由操作者选择是否启用，目标机标定/验收待完成 |

---

## 3. 数据面（Board/state.py）

### 3.1 核心数组

| 变量 | 长度 | 用途 | 初值 |
|---|---|---|---|
| `sensor_data[]` | 10 | 原始值，**显示通道专用**（999 透传） | 999 |
| `sensor_checked[]` | 10 | 校验值，**控制通道专用** | 0.0 |
| `sensor_quality[]` | 10 | Q_OK=0 / Q_SUSPECT=1 / Q_BAD=2 | Q_BAD |
| `sensor_checked_ms[]` | 10 | 最近 Q_OK 时刻 (ticks_ms) | 0 |
| `state_data[]` | **34** | 运行状态；索引 16-31 为 ESC 遥测，32/33 为流量/压缩机故障事件 | 见 §3.3 |
| `sensor_fault_reg` | u16 | 传感器故障位图（1=正常 0=异常），写者：Sensor 层 | 0xFFFF |
| `error_reg` | u32 | 系统故障位图，**写者：仅 supervisor** | 0xFFFFFFFF（1=正常，0=异常） |
| `supervisor_interlocks{}` | dict | supervisor→设备层联锁指令，已由 Compressor/Evaporator/Condenser/EXV 消费 | 全 False/0 |
| `control_params{}` | dict | EXV PID 参数 | 软件默认 |
| `compressor_control_params{}` | dict | 压缩机容量环 PID（Step5b） | 软件默认 |
| `protect_params{}` | dict | 保护阈值、`delay_slots[8]`、各 `*_DELAY_S`；`FLOW_ENABLE` 仅供后续水回路版本 | 保守初值；当前保持 0 |
| `adc_bits{}` | dict | **仅** pyb/machine ADC 通道位宽注册表 | PA4/PA5 = 12 |
| `esc_telemetry_cfg{}` | dict | **Step5a 新增**：各 ESC 极对数，用于原始电气转速→rpm | 极对数=7 |
| `manual_params{}` | dict | 手动模式参数（0x2/4x220/4x221/4x223） | 油门默认 1000；EXV 默认 30% |
| `evap_fan_user_params{}` | dict | 蒸发风机用户设定（4x222） | 油门 1500 |
| `driver_link_ok` | bool | Driver 链路状态，写者：main.DriverLink() | True |
| `fault_reset_request` | bool | 0x7 手动复位脉冲 | False |

### 3.2 传感器索引

```
IDX_SUCTION_T     = 0   回气温度 (PT100)
IDX_DISCHARGE_T   = 1   排气温度 (PT100)
IDX_INLET_T       = 2   蒸发器入口空气温度 (PT100)
IDX_OUTLET_T      = 3   蒸发器出口空气温度 (PT100，容量环被控量)
IDX_LIQUID_T      = 4   液管温度 (PT100)
IDX_SUCTION_PSI   = 5   低压压力 (ADC，软件绝压 bara)
IDX_DISCHARGE_PSI = 6   高压压力 (ADC，软件绝压 bara)
IDX_SUPERHEAT     = 7   过热度 SH（派生，父 = 0 与 5）
IDX_E_VALUE       = 8   焓值 E（派生，父 = 5 与 6）
IDX_CURRENT       = 9   整机电流 0-50A (ADC PA4)
```

### 3.3 状态索引（state_data 长度 34）

```
# 设备执行机构状态（0-15）
ST_MOTOR_SPEED        = 0   压缩机转速 r/min（写者：Compressor 模块）
ST_VALVE_POS          = 1   EXV 开度 %（写者：Valve_CRTL）
ST_VALVE_POS_STEPS    = 2   EXV 步数 0~500
ST_VALVE_FAULT        = 3   EXV 故障事件位（EXV 层写，supervisor 消费）
ST_EVAP_FAN_SPEED_CMD = 4   蒸发风机命令镜像（用户油门设定对应 4x222，RUNNING 期间）
ST_EVAP_FAN_RPM       = 5   蒸发风机转速反馈（写者：Evaporator 模块）
ST_EVAP_FAN_FAULT     = 6   蒸发风机故障事件位
ST_COND_FAN_SPEED_CMD = 7   冷凝风机转速指令
ST_COND_FAN_RPM       = 8   冷凝风机转速反馈（写者：Condenser 模块）
ST_COND_FAN_FAULT     = 9   冷凝风机故障事件位
ST_SENSOR_FAULT       = 10  传感器故障汇总（单 bit，见 PLAN C8）
ST_SYSTEM_ENABLE      = 11  系统使能（Modbus coil 0x1 写）
ST_MOTOR_SPEED_MIN    = 12  压缩机最低转速限制
ST_MOTOR_SPEED_MAX    = 13  压缩机最高转速限制
ST_FAN_SPEED_MIN      = 14  风机最低转速限制
ST_FAN_SPEED_MAX      = 15  风机最高转速限制

# ESC 遥测数据（16-31，Step5a 新增，2026-09-03）
# 写者：HAL/Driver.py update_telemetry_to_state()
# 999 = 无效数据（链路断、ESC 无响应等）

ST_ESC0_RPM           = 16  压缩机：反馈转速 (rpm)
ST_ESC0_TEMP          = 17  压缩机：ESC 温度 (°C)
ST_ESC0_VOLTAGE       = 18  压缩机：母线电压 (×10 V，如 240 = 24.0V)
ST_ESC0_CURRENT       = 19  压缩机：相电流 (×10 A，如 150 = 15.0A)

ST_ESC1_RPM           = 20  蒸发风机：反馈转速 (rpm)
ST_ESC1_TEMP          = 21  蒸发风机：ESC 温度
ST_ESC1_VOLTAGE       = 22  蒸发风机：母线电压
ST_ESC1_CURRENT       = 23  蒸发风机：固定 999（电流由 ACS712-50A 统一采样）

ST_ESC2_RPM           = 24  冷凝风机：反馈转速 (rpm)
ST_ESC2_TEMP          = 25  冷凝风机：ESC 温度
ST_ESC2_VOLTAGE       = 26  冷凝风机：母线电压
ST_ESC2_CURRENT       = 27  冷凝风机：固定 999

ST_ESC3_RPM           = 28  备用：反馈转速 (rpm)
ST_ESC3_TEMP          = 29  备用：ESC 温度
ST_ESC3_VOLTAGE       = 30  备用：母线电压
ST_ESC3_CURRENT       = 31  备用：相电流

ST_FLOW_SWITCH        = 32  后续带水回路版本流量开关；当前 `di_flow` 为软件占位而非实体 DI
ST_COMPRESSOR_FAULT   = 33  压缩机故障事件位
```

### 3.4 API

```python
sensor_age_ms(idx)          # 距最近 Q_OK 的 ms；未更新过返回 10**9
set_sensor_fault(name)      # 需 global sensor_fault_reg
clear_sensor_fault(name)    # 需 global sensor_fault_reg
update_all_sensor_faults()  # 按 sensor_data 批量刷新
set_fault(name) / clear_fault(name) / is_fault(name)   # 仅 supervisor 可调
```

---

## 4. 运行时结构

### 4.1 协程（main.py `asyncio.gather`）

| 协程 | 周期 | 说明 |
|---|---|---|
| `Sensor()` | 200ms（墙上 ~760ms） | `reads_async()` + `validate()` |
| `supervisor_loop()` | 100ms | 规则评估 + 状态机 |
| `DriverLink()` | 5ms sleep | poll_status + send_all_commands，空载 |
| `Exv()` | pid 1s / valve 100ms | gather 两子循环 |
| `slave_loop()` | 事件驱动 | Modbus 收帧 |
| `sync_loop()` | 200ms | Modbus 镜像同步 |
| `Display()` | 1s | 测试屏 |
| `Compressor()` | 0.5s | 压缩机执行层 + 容量环 |
| `params_store.loop()` | 1s | Step6 参数掉电保持（30s 防抖落盘）|

### 4.2 数据流与单一写者

```
硬件 → reads_async() → sensor_data[]  ──→ 显示通道（999 透传）
                            ↓
                        validate()  → sensor_checked / quality / checked_ms
                            ↓                    ↓
                 update_all_sensor_faults   控制通道（先查 quality+age）
                            ↓                    ↓
                     sensor_fault_reg      rules.py → supervisor.tick()
                                                        ↓
                                          error_reg / sys_mode / interlocks
                                                        ↓
                                          supervisor_interlocks → Compressor/Evaporator/Condenser/EXV 四模块消费
```

| 数据 | 唯一写者 |
|---|---|
| `sensor_data` | Sensor 协程 |
| `sensor_checked` / `quality` / `checked_ms` | `validate()` |
| `sensor_fault_reg` | Sensor 层 `update_all_sensor_faults()` |
| `error_reg` | **仅 `Alarm/supervisor.py`**（全代码扫描已验证） |
| `supervisor_interlocks` | supervisor |
| `driver_link_ok` | `main.DriverLink()` |
| `state_data[0]` ST_MOTOR_SPEED | Compressor 模块（Step5a 已实现） |
| `state_data[1-3]` EXV 相关 | Valve_CRTL / EXV 模块 |
| `state_data[5]` ST_EVAP_FAN_RPM | Evaporator 模块（Step5a 已实现） |
| `state_data[8]` ST_COND_FAN_RPM | Condenser 模块（Step5a 已实现） |
| `state_data[16-31]` ESC 遥测 | **HAL/Driver.py `update_telemetry_to_state()`**（Step5a 已实现） |

### 4.3 硬件资源

| 资源 | 用途 | 参数 |
|---|---|---|
| UART1 | 测试屏（A5 5A） | 115200 |
| UART6 | Pico ESC 链路 | 921600 |
| UART7 | EXV TMC2209 | 115200 |
| UART8 | Modbus RTU 从机 | 9600 + RS485 |
| SPI1 | AD7124（5×PT100） | CS=PG15 |
| ADC | PC2/PC3 压力（adc_direct，16-bit）、PA4 电流 / PA5 备用（pyb.ADC，12-bit） | — |
| TIM4 CH2 | TMC2209 STEP | — |
| GPIO | EXV DIR/EN | PE10 / PE12 |

ESC 分配：ESC0 压缩机 / ESC1 蒸发风机 / ESC2 冷凝风机 / ESC3 备用。

---

## 5. docs/ 与查阅入口

| 文件 | 内容 | 何时看 |
|---|---|---|
| `README.md` | 文档导航、推荐阅读路径及各地图的职责边界 | 首次阅读文档时 |
| `DESIGN.md` | **统一设计规范**：硬件资源、数据接口、核心原则、状态机、启停时序、报警保护、三环控制、手动模式、参数体系、Modbus 接口、已拍板决策；附录 B/C 收录已实施的 Modbus 重构与焓值改造计划原文 | **写代码前必读**，查设计约定 |
| `PLAN.md` | Step1-7 计划、DoD、进度节点、**全部问题登记表** | 想知道现在做到哪、还有什么问题 |
| `REPORT.md` | 各阶段验收报告与审计结论、测试结果；附录 A-F 完整收录历史审计/测试报告原文 | 想知道某个 Step 做了什么、怎么验的 |
| `STRUCTURE.md` | 本文档：目录、模块职责、数据面 | 想找文件、查索引常量 |
| `STARTUP_FLOW.md` | **启动与运行过程图（分步版）**：上电初始化、gather 首轮调度、sys_mode 状态机、STARTUP S0-S6、RUNNING 三环、停机/故障/复位、代码现状差异 | 调试启动流程、验收上电时序 |
| `SYSTEM_FULL_MAP.md` | **全系统合并大图**：一页详细总览 + 全阶段总表（A-I 九张分组 Mermaid，便于逐组查看）+ 一张超级大 Mermaid（启动/运行/停机/点位四区全整合）+ 点位明细 | 想看全系统一张图、逐组查启动与点位对应关系 |
| `IO_MAP.md` | **硬件 I/O 与信号映射**：物理传感器/引脚/总线 → 软件变量 → 控制/保护消费者 → 执行器与反馈；未确认的接线明确标注 | 查 MCU 物理输入输出、软件地址与设备通道的对应关系 |
| `MODBUS_POINT_TABLE.md` | Modbus 点位表：hr/ir/di 完整映射、威纶通配置 | 配置触摸屏、查看寄存器地址 |
| `MODBUS_ADDRESS_MAP.md` | 内部地址/对外地址/读写/功能详细说明 | 查地址换算与写入权限 |
| `AI_DEVELOPMENT_RULES.md` | 开发强制约束与自查清单 | 动代码前 |
| `archive/` | 已并入 DESIGN/REPORT 的历史计划与审计报告原始文件 | 仅需核对合并前原始排版时 |

**首次接手推荐顺序**：`README.md`（文档导航）→ `AI_DEVELOPMENT_RULES.md` → `STRUCTURE.md` → `SYSTEM_FULL_MAP.md` → `IO_MAP.md` → `PLAN.md`（当前进度+问题）→ `DESIGN.md`（设计规范）。

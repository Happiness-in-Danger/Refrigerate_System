# 制冷系统统一设计说明

**用途**：设计、代码接口和点位的统一入口。本文档合并 ARCHITECTURE_PLAN.md、SYSTEM_LOGIC_DESIGN.md、THROTTLE_DESIGN.md 的有效设计内容。
**核对日期**：2026-10-11  
**版本**：v3.3（外环输出油门；转速环按独立使能开关提供转速约束；空冷直膨样机边界于 2026-10-11 明确）

---

## 文档说明

本文档是**唯一的设计规范入口**，整理并维护以下历史设计稿中的有效决策；历史文件名仅作来源说明，当前开发以本文及其链接的权威点表为准：

- `ARCHITECTURE_PLAN.md` v2.5：架构设计基准、保护规则、系统主逻辑
- `SYSTEM_LOGIC_DESIGN.md`：系统控制逻辑设计
- `THROTTLE_DESIGN.md`：油门/转速环设计及变更记录
- `MODBUS_REFACTOR_PLAN.md`：Modbus 点表重构计划（已实施），完整原文见附录 B
- `ENTHALPY_ADDITION_PLAN.md`：液管焓值添加改造计划（已实施），完整原文见附录 C

历史设计稿与当前实现有差异时，以本文件中标注的当前口径和实现状态为准；已确认的实现调整应及时同步到本文件。

相关文档：
- 进度与问题台账 → `PLAN.md`
- 验收报告 → `REPORT.md`
- 文件结构与索引 → `STRUCTURE.md`
- Modbus 点位表 → `MODBUS_POINT_TABLE.md`

---

## 目录

1. 当前状态
2. 硬件资源与平台
3. 数据接口
4. 核心设计原则
5. 状态机与启停时序
6. 报警与保护策略
7. 系统主逻辑（三环控制）
8. 手动控制模式
9. 参数体系
10. Modbus 接口
11. 已拍板决策记录

---

## 1. 当前状态

### 1.1 平台与工具链

- **主控**：STM32H743 + MicroPython 自编译固件（`adc_direct` 冻结在内）
- **运行时**：单线程 `uasyncio` 事件循环，协作式调度
- **开发平台**：F407 仅用于前期模块调试，整机目标平台已全部迁移到 H743

### 1.2 执行器配置

- **ESC0**：压缩机（Pico + 无刷 ESC，DShot 遥测，UART6）
- **ESC1**：蒸发风机（Pico + 无刷 ESC）
- **ESC2**：冷凝风机（Pico + 无刷 ESC）
- **ESC3**：备用
- **EXV**：步进电机 + TMC2209（UART7 单线 + TIM4 PWM 脉冲）

### 1.3 系统类型与样机范围

- **当前样机**：风冷式直膨制冷；蒸发侧制冷剂直接在蒸发器内换热，冷凝侧由风机散热；当前方案无冷水/载冷剂回路
- **被控变量**：蒸发器出口空气温度（`IDX_OUTLET_T`）；`IDX_INLET_T` 为蒸发器入口空气温度
- **控制目标**：`setpoint = 22.0°C`（Modbus 4x19 可调，×100 定点）
- **当前样机不含制热或除霜功能**：无四通阀；除霜暂不纳入样机功能，但必须限定并验证运行工况，确保蒸发器不会进入结霜工况。若后续范围包含低温/高湿结霜工况，应增加结霜检测及除霜方案
- **后续扩展项**：水流开关/水流保护属于带水回路版本的预留功能；不是当前空冷直膨样机的已接入硬件功能

### 1.4 代码完成度

- **Step1-4**：已封板（检查层、Alarm/supervisor、Driver link 去阻塞）
- **Step5a**：已封板（设备执行层、转速环架构 v3.3、手动模式、启停时序）
- **Step5b**：已封板（三环控制、温控循环、L1 限载、排气温度保护、重试锁定）
- **Step6**：已封板（参数单源、Modbus 全量寄存器映射、B6 周期重标、C8 多 bit、持久化）
- **Step7**：代码 DoD 完成（Desplay→Display 更名、A8 修复、孤儿清单销账）；实测 DoD 待目标机
- **样机范围补充（2026-10-11）**：当前为空冷式直膨、蒸发侧空气换热，无水回路；`FLOW_FAULT` 规则和点位仅为后续带水回路版本预留。当前 `di_flow` 是软件占位，不能作为实物流量保护
- **Modbus 点表重构**：已实施（2026-09-19，标准四区模型 0x/1x/3x/4x；2026-09-22 v4.1 新增 3x10 进口焓 / 3x11 出口焓），详见 §10 与附录 B/C
- **测试状态**：329/329 PASS（Step2 25 / Step3 30 / Step4 23 / Step5a 27 / Step5b 56 / Step6 96 / Step7 72）

**注意**：本文档区分"代码现状"和"目标设计"，不得把伪代码或计划项当作已实现功能。

---

## 2. 硬件资源与平台

### 2.1 硬件资源分配表

| 资源 | 用途 | 引脚/参数 | 状态 |
|---|---|---|---|
| UART1 | 组态屏（测试方案，A5 5A 协议） | 115200 | 运行中（Display.py） |
| UART6 | Pico ESC 链路（Driver.py） | 921600 | 已配置 |
| UART7 | EXV TMC2209 | 115200 | 已配置 |
| UART8 | Modbus RTU 从机（正式方案） | 9600，RS485 转接板 | 已配置 |
| SPI1 | AD7124（5×PT100） | CS=PG15 | 正常 |
| TIM4 CH2 | TMC2209 STEP 脉冲 | PD13 | 正常 |
| GPIO | EXV DIR/EN | PE10 / PE12 | 正常 |
| ADC | 压力×2（PC2/PC3，adc_direct 16-bit）、电流（PA4，pyb.ADC 12-bit）、备用（PA5） | — | PC2/PC3 已跑通；PA4 采集代码已实现 |

### 2.2 传感器配置

- **5×PT100**（AD7124，SPI1）：压缩机吸气温度、排气温度、蒸发器入口空气温度、蒸发器出口空气温度、制冷剂液管温度
- **2×压力传感器**（ADC PC2/PC3）：硬件为表压型探头，**软件全程绝对压力 (bara)**
- **1×整机电流**（ADC PA4）：ACS712-50A，量程 0-50A

**压力软件量全程绝对压力 (bara)**：`psi_trans` 已做表压→绝压（+大气压）；state / validate / Alarm / Modbus 阈值一律绝压，不得再当表压用。

---

## 3. 数据接口

### 3.1 传感器索引

`sensor_data[0..9]` 是原始显示值，故障哨兵 `999` 原样保留；控制逻辑只能读取 `sensor_checked`，并先检查 `sensor_quality == Q_OK` 和 `sensor_age_ms()`。

| 索引 | 常量 | 含义 | 单位 |
|---:|---|---|---|
| 0 | `IDX_SUCTION_T` | 压缩机吸气温度 | °C |
| 1 | `IDX_DISCHARGE_T` | 排气温度 | °C |
| 2 | `IDX_INLET_T` | 蒸发器入口空气温度 | °C |
| 3 | `IDX_OUTLET_T` | **蒸发器出口空气温度，容量环被控变量** | °C |
| 4 | `IDX_LIQUID_T` | 液管温度 | °C |
| 5 | `IDX_SUCTION_PSI` | 吸气压力 | bara |
| 6 | `IDX_DISCHARGE_PSI` | 排气压力 | bara |
| 7 | `IDX_SUPERHEAT` | 过热度（派生） | K |
| 8 | `IDX_E_VALUE` | 吸气焓（派生，`read_E(吸气压力, 吸气温度)`，映射 3x10） | kJ/kg |
| 9 | `IDX_CURRENT` | 整机总电流 | A |

**派生标量（不占 `sensor_data` 索引，2026-09-19/22 新增）**：

| 变量 | 含义 | 计算 | Modbus |
|---|---|---|---|
| `state.subcooling` | 过冷度 | `饱和温度(排气压力) - 液管温度` | 3x9（×10） |
| `state.liquid_enthalpy` | 液管焓（出口焓） | `read_E(排气压力, 液管温度)` | 3x11（×100） |

> 两个派生标量由 `read_sensors.py` 计算（异常置 999），**不在 `sensor_checked`/`sensor_quality`/`sensor_age_ms` 质量跟踪体系内**，无 BAD 判定与故障位——仅作显示通道使用。若未来用于制冷量/COP 计算，需先补质量门禁。

### 3.2 状态索引 (state_data)

`state_data[]` 长度 34（Step5a 扩展 16→32；流量开关/压缩机故障事件位扩展至 34）：

**设备执行机构状态（0-15）**：

| 索引 | 常量 | 含义 | 单位 | 写者 |
|---:|---|---|---|---|
| 0 | `ST_MOTOR_SPEED` | 压缩机转速反馈 | r/min | Compressor 模块 |
| 1 | `ST_VALVE_POS` | EXV 开度 | % | Valve_CRTL |
| 2 | `ST_VALVE_POS_STEPS` | EXV 步数 | steps | Valve_CRTL |
| 3 | `ST_VALVE_FAULT` | EXV 故障事件位 | 0/1 | EXV 层 |
| 4 | `ST_EVAP_FAN_SPEED_CMD` | 蒸发风机用户设定（hr[221]，即 4x222） | r/min | Modbus |
| 5 | `ST_EVAP_FAN_RPM` | 蒸发风机转速反馈 | r/min | Evaporator 模块 |
| 6 | `ST_EVAP_FAN_FAULT` | 蒸发风机故障事件位 | 0/1 | Evaporator 模块 |
| 7 | `ST_COND_FAN_SPEED_CMD` | 冷凝风机转速指令 | r/min | Condenser 模块 |
| 8 | `ST_COND_FAN_RPM` | 冷凝风机转速反馈 | r/min | Condenser 模块 |
| 9 | `ST_COND_FAN_FAULT` | 冷凝风机故障事件位 | 0/1 | Condenser 模块 |
| 10 | `ST_SENSOR_FAULT` | 传感器故障汇总（单 bit，Step6 扩展为 16-bit） | 0/1 | supervisor |
| 11 | `ST_SYSTEM_ENABLE` | 系统使能（coils 0x1） | 0/1 | Modbus |
| 12 | `ST_MOTOR_SPEED_MIN` | 压缩机最低转速限制 | r/min | Modbus |
| 13 | `ST_MOTOR_SPEED_MAX` | 压缩机最高转速限制 | r/min | Modbus |
| 14 | `ST_FAN_SPEED_MIN` | 风机最低转速限制 | r/min | Modbus |
| 15 | `ST_FAN_SPEED_MAX` | 风机最高转速限制 | r/min | Modbus |

**ESC 遥测数据（16-31，Step5a 新增）**：

| 索引 | 常量 | 含义 | 单位 | 写者 |
|---:|---|---|---|---|
| 16 | `ST_ESC0_RPM` | 压缩机反馈转速 | rpm | Driver link 协程 |
| 17 | `ST_ESC0_TEMP` | 压缩机 ESC 温度 | °C | Driver link 协程 |
| 18 | `ST_ESC0_VOLTAGE` | 压缩机母线电压 | V (×10) | Driver link 协程 |
| 19 | `ST_ESC0_CURRENT` | 压缩机相电流 | A (×10) | Driver link 协程 |
| 20 | `ST_ESC1_RPM` | 蒸发风机反馈转速 | rpm | Driver link 协程 |
| 21 | `ST_ESC1_TEMP` | 蒸发风机 ESC 温度 | °C | Driver link 协程 |
| 22 | `ST_ESC1_VOLTAGE` | 蒸发风机母线电压 | V (×10) | Driver link 协程 |
| 23 | `ST_ESC1_CURRENT` | 蒸发风机相电流（本机无效） | A (×10) | 固定 999 |
| 24 | `ST_ESC2_RPM` | 冷凝风机反馈转速 | rpm | Driver link 协程 |
| 25 | `ST_ESC2_TEMP` | 冷凝风机 ESC 温度 | °C | Driver link 协程 |
| 26 | `ST_ESC2_VOLTAGE` | 冷凝风机母线电压 | V (×10) | Driver link 协程 |
| 27 | `ST_ESC2_CURRENT` | 冷凝风机相电流（本机无效） | A (×10) | 固定 999 |
| 28 | `ST_ESC3_RPM` | 备用 ESC 反馈转速 | rpm | Driver link 协程 |
| 29 | `ST_ESC3_TEMP` | 备用 ESC 温度 | °C | Driver link 协程 |
| 30 | `ST_ESC3_VOLTAGE` | 备用母线电压 | V (×10) | Driver link 协程 |
| 31 | `ST_ESC3_CURRENT` | 备用相电流 | A (×10) | Driver link 协程 |

**扩展状态（32-33）**：

| 索引 | 常量 | 含义 | 单位 | 写者 |
|---:|---|---|---|---|
| 32 | `ST_FLOW_SWITCH` | 后续带水回路版本的流量开关状态；当前 `board.di_flow` 是软件占位，非实体 DI | 0/1 | 当前软件占位/测试注入 |
| 33 | `ST_COMPRESSOR_FAULT` | 压缩机故障事件位（供 rules.py COMPRESSOR_FAULT 规则消费） | 0/1 | Compressor 模块 |

遥测无效值为 `999`。`ST_MOTOR_SPEED`、`ST_EVAP_FAN_RPM`、`ST_COND_FAN_RPM` 和 `ST_ESC*_RPM` 均表示反馈转速，不是油门值。

### 3.3 supervisor 联锁

`supervisor_interlocks` 是 supervisor 模块输出给设备执行层的实时指令信号：

```python
{
    "comp_enable": bool,                 # 压缩机使能门禁
    "comp_throttle_cmd": 0..2047,        # 压缩机油门指令（v3.3）
    "evap_fan_enable": bool,             # 蒸发风机使能门禁
    "evap_fan_throttle_cmd": 0..2047,    # 蒸发风机油门指令（v3.3）
    "cond_fan_enable": bool,             # 冷凝风机使能门禁
    "cond_fan_throttle_cmd": 0..2047,    # 冷凝风机油门指令（v3.3）
    "exv_enable": bool,                  # EXV 使能门禁
    "exv_target_pct": 0.0..100.0,        # EXV 目标开度（%）
}
```

**控制架构（当前实现口径）**：
- **控制架构**：外环（容量环/压力环/用户设定）输出油门绝对值；执行层将基础油门映射为目标转速，转速环按独立开关决定是否根据转速反馈闭环调节最终油门
- **STARTUP 时期**：supervisor 直接输出 `*_throttle_cmd` 恒定油门（如 `COMP_START_THROTTLE = 300`）
- **RUNNING 时期**：
  - 容量环（外环）输出 `comp_throttle_base`（油门绝对值 48-2047）
  - 冷凝压力环输出 `cond_fan_throttle_base`（油门绝对值 48-2047）
  - 蒸发风机用户设定（hr[221]，即 4x222）输出 `evap_fan_throttle_base`（油门绝对值 48-2047）
- **转速环（独立使能的转速约束环）**：
  - 作用：按基础油门对应的目标转速调节 ESC 油门，使反馈转速受目标约束；承担转速保护作用，但当前实现不只是超速时才介入的单向限幅器
  - 输入：基础油门换算出的 `rpm_cmd`、当前转速反馈 `rpm_current`
  - 输出：增量式 PID 调整后的最终油门；单次调节量受 `throttle_max_adjust` 限制
  - 目标换算：`rpm_cmd = throttle_base / ESC_THROTTLE_MAX * rpm_max`
  - 独立开关：`COMP_SPEED_LOOP_ENABLE` / `COND_FAN_SPEED_LOOP_ENABLE` / `EVAP_FAN_SPEED_LOOP_ENABLE`
  - 手动模式下仍服从各自开关：开启时对手动基础油门对应的目标转速闭环调节；关闭时按手动油门开环输出。该开关有意保留给操作者，不因手动模式而强制开或关

**ESC 油门约束**：
- 停机时油门必须为 `0`
- 运行时有效油门范围为 `48-2047`（`1-47` 为无效区间）
- 最终油门由 `HAL/Driver.py` 做最终限幅保护

**命名规范**：
- supervisor/外环输出为 `*_throttle_base`（基础油门，绝对值）
- 转速环输出为 `*_throttle_adjust`（调节量，可正可负）
- 最终输出为 `*_throttle_cmd`（发送给 ESC 的油门）
- ESC 上行遥测是反馈转速 `rpm`，由 Driver 协议帧中的原始 `erpm` 按极对数换算得到
- 对上层只提供 `rpm`，不暴露 `erpm`

### 3.4 故障寄存器

`error_reg` 是 32 位反向逻辑位图：`1=正常`、`0=异常`，正常值为 `0xFFFFFFFF`。

**位分配**（`Board/state.py` `ERROR_BITS`，2026-09-19 重构删除 LCD_COMM_FAULT 后 bit8-15 前移）：

| 位 | 名称 | 含义 |
|---:|---|---|
| 0 | `EXV_FAULT` | EXV 归零失败 / TMC2209 驱动器故障 |
| 1 | `COMPRESSOR_FAULT` | 压缩机故障（使能有油门但反馈≈0） |
| 2 | `EVAP_FAN_FAULT` | 蒸发风机转速异常 |
| 3 | `COND_FAN_FAULT` | 冷凝风机转速异常 |
| 4 | `HIGH_PRESSURE_FAULT` | 高压保护（排气压力超限） |
| 5 | `LOW_PRESSURE_FAULT` | 低压保护（吸气压力过低） |
| 6 | `HIGH_DISCHARGE_TEMP` | 排气温度过高停机（TD_TRIP 持续 300s） |
| 7 | `OVERCURRENT_FAULT` | 整机电流过流 |
| 8 | `MODBUS_COMM_FAULT` | Modbus 通讯故障（**预留**：当前规则 `bit=None` 不置位，通讯断走正常停机不锁存，见 §6.5） |
| 9 | `LOW_SUPERHEAT_FAULT` | 过热度过低（液击风险） |
| 10 | `CONFIG_IO_FAULT` | config.json 读写失败 / 分级阈值单调性校验失败 |
| 11 | `SUCTION_PSI_FAULT` | 吸气压力传感器 BAD |
| 12 | `DISCHARGE_PSI_FAULT` | 排气压力传感器 BAD |
| 13 | `CURRENT_SENSOR_FAULT` | 电流传感器 BAD |
| 14 | `SENSOR_STALE_FAULT` | 全通道超龄 / Sensor 心跳丢失 |
| 15 | `FLOW_FAULT` | 后续带水回路版本的流量故障；当前样机不使用 |
| 16-31 | — | 未定义（保留） |

**对外映射（双通道）**：
- 低 16 位按位镜像到离散输入 1x1-1x16（FC02，1=正常 0=故障）
- 完整 32 位拆分为低/高 16 字 → 3x110 / 3x111（bit16-31 仅经此通道暴露）
- Driver 链路断不占 error_reg 位，由 supervisor 维护 `driver_link_latched` 软标志 → 1x100
- 只有 `Alarm/supervisor.py` 可以调用 `set_fault()` / `clear_fault()`

---

## 4. 核心设计原则

### 4.1 数据校验与显示通道分离

**全局规约**：
- **显示通道**：永远看原始值 + 故障寄存器（999 原样透传，给人看报警）
  - Modbus input_regs（组态屏/上位机）
  - UART1 测试屏
- **控制通道**：永远看校验值 + 质量码（给机器做决策）
  - EXV PID、Alarm 规则、supervisor、压缩机/风机模块
  - 先查 `sensor_quality` 与 `sensor_age_ms()`，再取 `sensor_checked` 数值

质量码三级：`Q_OK=0` / `Q_SUSPECT=1` / `Q_BAD=2`

> **显示专用点位原则**：点表（§10）中部分点位**仅用于人机交互显示，本就没有代码消费者**——
> 如 ESC 遥测镜像、系统状态/联锁镜像、过冷度/进出口焓等派生量。这些点位由写者
> （Driver link / supervisor / Sensor 层）单向发布到 3x/1x 区供触摸屏与上位机读取，
> 属于"显示通道"的正常组成，**审计时不应计为"未消费参数"缺陷**。
> 只有写方向的 4x 参数无任何消费者（既不控制也不显示）时，才算真正的死点位。

### 4.2 控制架构（v3.3）

**外环（慢环）**：输出油门绝对值（throttle_base）
- 容量环（10s）：蒸发器出口空气温度 → 压缩机油门（throttle_base）
- 冷凝压力环（2s）：排气压力 → 冷凝风机油门（throttle_base）
- 过热度环（1s）：过热度 → EXV 开度
- 蒸发风机：用户设定（hr[221]，即 4x222）→ 油门（throttle_base）

**转速环（0.5s，可独立启用/禁用）**：基础油门映射为目标转速，转速反馈环调节最终油门
- 压缩机：`comp_throttle_base` → `rpm_cmd`；`ST_ESC0_RPM` → 转速 PID → ESC0 油门
- 冷凝风机：`cond_fan_throttle_base` → `rpm_cmd`；`ST_ESC2_RPM` → 转速 PID → ESC2 油门
- 蒸发风机：用户/启动油门 → `rpm_cmd`；`ST_ESC1_RPM` → 转速 PID → ESC1 油门
- 三个独立开关由操作者控制；手动模式不改变开关语义

**控制流**：
```
外环（温度/压力） → throttle_base（油门绝对值）
                        ↓
转速环（可选）→ 转速闭环调节油门
                        ↓
             throttle_final（PID 调节并限幅）→ ESC
                        ↑
                   ESC 遥测反馈 rpm
```

**关键设计**：
- 外环直接输出油门绝对值（48-2047），基于启动默认油门累加 PID 输出
- 转速环按基础油门映射出的 `rpm_cmd` 闭环调节油门；其用途是转速约束/保护，当前实现会在运行区间内持续调节，不限定为接近上限时的单向负调节
- 转速环死区默认 200 rpm，单次 PID 调节量由 `throttle_max_adjust` 限制（默认 100 throttle）
- 外环和转速环解耦：外环更新周期长（2-10s），转速环持续运行（0.5s）
- 油门最终限幅在 throttle_final 层面实施（0-2047）

**所有执行机构的指令都是油门值（throttle），不是转速（rpm）**：

| 执行机构 | 指令类型 | 范围 | 说明 |
|---|---|---|---|
| 压缩机 | throttle | 48-2047（停机为 0） | 无刷 ESC 油门值 |
| 蒸发风机 | throttle | 48-2047（停机为 0） | 无刷 ESC 油门值 |
| 冷凝风机 | throttle | 48-2047（停机为 0） | 无刷 ESC 油门值 |
| EXV | 开度百分比 | 0.0-100.0 | 步进电机，不是油门 |

**所有 ESC 的反馈都是转速（rpm），不是油门值**：

| 反馈数据 | 类型 | 来源 | 存储位置 |
|---|---|---|---|
| 压缩机转速 | rpm | ESC0 遥测 | state_data[ST_ESC0_RPM] |
| 蒸发风机转速 | rpm | ESC1 遥测 | state_data[ST_ESC1_RPM] |
| 冷凝风机转速 | rpm | ESC2 遥测 | state_data[ST_ESC2_RPM] |

### 4.3 单一写者原则

| 数据 | 唯一写者 |
|---|---|
| `sensor_data` | Sensor 协程 |
| `sensor_checked` / `quality` / `checked_ms` | `validate()` |
| `sensor_fault_reg` | Sensor 层 `update_all_sensor_faults()` |
| `error_reg` | **仅 `Alarm/supervisor.py`** |
| `supervisor_interlocks` | supervisor |
| `driver_link_ok` | `main.DriverLink()` |
| `state_data[16-31]` ESC 遥测 | `HAL/Driver.py` link 协程 |

**C8 问题（✅ Step6 已修复）**：
- **原状态**：`ST_SENSOR_FAULT`（索引 10）为单 bit，仅区分"有故障"和"无故障"
- **数据源**：`sensor_fault_reg` 已是 16-bit 寄存器（5 路传感器 + 2 路压力 + 1 路电流 = 8 位有效）
- **Step6 修复**：
  - 保留 `ST_SENSOR_FAULT` 作为汇总标志（向后兼容）
  - Modbus 3x17 映射完整 16-bit `sensor_fault_reg`（`modbus_slave.sync_input_regs()`）
  - 1x12-1x15 由 error_reg 位 11-14 镜像承载"吸气/排气压力、电流传感器 BAD、传感器超龄"
- 位定义顺序：bit0=吸气温度、bit1=排气温度、bit2=蒸发器入口空气温度、bit3=蒸发器出口空气温度、bit4=液管温度、bit5=高压、bit6=低压、bit7=电流、bit8-15=保留

### 4.4 全延时原则

**任意报警条件 → 执行动作，必须先满足该规则的延时；无"零延时跳闸"。**

延时通过 8 个共享延时槽管理：`delay_slots[0..7]`（单位：秒），槽值可由 Modbus 配置。

---

## 5. 状态机与启停时序

### 5.1 sys_mode 状态机

```
BOOT → SELF_TEST（外设显式 init，失败进 DEGRADED）
     → SAFE_STOP（默认待机：EXV 归零、全 ESC 禁用）
     → STARTUP（联锁满足：无严重故障 + ST_SYSTEM_ENABLE=1）
     → RUNNING（各设备控制环投运）
          ├─ ALARM（可恢复故障，继续运行+记录）→ 恢复回 RUNNING
          └─ 严重故障 → SAFE_STOP（按停机时序）→ 确认后允许重试
```

### 5.2 STARTUP 启动时序（S0-S6）

**启动顺序（2026-09-07 修订，增加转速环）**：

| 步 | 动作 | 成功判据 | 失败 |
|---|---|---|---|
| S0 | 进入 STARTUP；锁存启动请求 | sys_mode=STARTUP | — |
| S1 | 冷凝风机固定油门 → `COND_FAN_START_THROTTLE` | 反馈转速有效（`ST_ESC2_RPM` > 0） | 延时确认后告警/停机 |
| S2 | 蒸发风机固定油门 → `EVAP_FAN_START_THROTTLE` | 反馈转速有效（`ST_ESC1_RPM` > 0） | 延时确认后告警/停机 |
| S3 | EXV 到固定开度 `EXV_START_POS`（% 或步数） | 到位且无 EXV_FAULT | 延时确认后 → SAFE_STOP |
| S4 | **EXV 开始动作后延时** `delay_slots[EXV_TO_COMP_DELAY_SLOT]` 秒，再以固定启动油门启动压缩机 | 反馈转速有效（RPM 环启用时闭环调节启动油门对应目标） | 超时/无反馈 → SAFE_STOP |
| S5 | 系统层晋级（二选一，coils 0x8 可配）| 晋级条件满足 | 超时 → SAFE_STOP |
| S6 | 进入 RUNNING；EXV 过热度环使能；各保护武装 | sys_mode=RUNNING | — |

**启动时油门控制（当前实现）**：
- **S1/S2**：supervisor 输出固定基础油门（`COND_FAN_START_THROTTLE` / `EVAP_FAN_START_THROTTLE`）；对应转速环若启用，设备模块按映射目标转速闭环调节
- **S4 启动**：压缩机以恒定基础油门启动（`COMP_START_THROTTLE`），无油门斜坡；转速环若启用，则闭环调节该基础油门映射出的目标转速
- **转速环**：STARTUP 期间若启用，同样按基础油门映射目标转速并闭环调节；若禁用则开环下发启动油门

**启动两层（不冲突）**：

| 层 | 职责 | 谁实现 |
|---|---|---|
| **压缩机启动** | 输出恒定启动油门：`COMP_START_THROTTLE` | `Alarm/supervisor` |
| **系统晋级 S5** | 用转速稳定性判定"可以进入 RUNNING" | `Alarm/supervisor` |

**S5 晋级模式** `STARTUP_ADVANCE_MODE`（coils 0x8 可写）：

| 模式 | 值 | 晋级条件 |
|---|---|---|
| 定速保持（旧模式） | 0 | 反馈在目标转速窗口内连续保持 `COMP_HOLD_S` 秒 |
| 转速稳定性检查（推荐） | 1 | 启动后延时 `COMP_START_DELAY_S` 秒，然后在 `COMP_RPM_STABILITY_WINDOW_S` 秒窗口内每秒采样，转速变化 < `COMP_RPM_STABILITY_THRESHOLD` 即判定稳定 |

**晋级判据详细说明（模式 1，v3.3 新增）**：
1. 压缩机启动后，以恒定油门 `COMP_START_THROTTLE` 运行
2. 等待 `COMP_START_DELAY_S`（默认 15 秒）后，开始转速稳定性检查
3. 在接下来的 `COMP_RPM_STABILITY_WINDOW_S`（默认 10 秒）窗口内：
   - 每隔 `COMP_RPM_STABILITY_SAMPLE_S`（默认 1 秒）采样一次转速
   - 记录窗口内的最大转速和最小转速
   - 如果 `rpm_max - rpm_min <= COMP_RPM_STABILITY_THRESHOLD`（默认 100 rpm），判定为稳定
4. 稳定后晋级到 RUNNING

**关键点**：
- EXV → 压缩机延时：默认约 5s（槽 1），用户可配
- STARTUP 内 EXV 固定开度，不投过热度 PID；PID 仅 S6→RUNNING 后投运
- **STARTUP 内不做吸气低压（示值/传感器 BAD）和 L1 限载判定**
- 转速环在 STARTUP 期间的开关语义与 RUNNING 相同；启用时闭环跟踪启动油门映射出的目标转速，禁用时开环输出

### 5.3 正常停机时序（N0-N4）

| 步 | t= | 动作 |
|---|---|---|
| N0 | 0 | 接受停机请求 |
| N1 | 0 | EXV 开始关闭（关至安全位/归零） |
| N2 | N1+`delay_slots[COMP_STOP_DELAY_SLOT]` | 压缩机 ESC0：throttle→0 并 disable |
| N3 | N2+`delay_slots[FAN_STOP_DELAY_SLOT]` | 蒸发风机、冷凝风机停 |
| N4 | 全部完成 | sys_mode=SAFE_STOP；全 ESC disable 确认 |

默认延时 5s（槽 1），用户可在屏幕指定槽号。

### 5.4 故障停机时序（E0-E4）

**入口**：某条 Alarm 规则条件连续成立达到其 `delay_slots[X]` 之后，才进入本序列。

| 步 | t= | 动作 |
|---|---|---|
| E0 | 0 | **锁存**对应 `error_reg` 位；开始执行机构动作 |
| E1 | 0 | 压缩机 throttle=0 且 disable；高压 → 冷凝风机全开排热 |
| E2 | E0+`FAULT_FAN_HOLD_MS`（默认 15000，高压） | 风机收至安全/停止 |
| E3 | E0 起 | EXV：低过热度类可关小；其余保持或归零 |
| E4 | 序列结束 | sys_mode=SAFE_STOP；**禁止自动清故障、禁止自动重启** |

### 5.5 故障复位协议

```
故障延时确认 → supervisor 锁存 error_reg 对应位为异常(0)
            → 组态屏显示报警
            → 操作者在触摸屏点"故障复位"（coils 0x7 写 1 脉冲，自动清零、回读恒 0）
            → supervisor 对该位尝试 clear
            → 下一周期重新跑 Alarm 判定：
                 条件已消失 → 位保持正常(1)，允许再次 STARTUP
                 条件仍在   → 立即再次置位异常(0)
```

- **清故障唯一入口**：触摸屏手动复位（coils 0x7）
- 程序在复位之后**只负责再判定**
- `ST_SYSTEM_ENABLE` 与故障复位分离：复位不等于开机

---

## 6. 报警与保护策略

### 6.1 报警分级

| 级 | 名称 | 语义 | 动作 | error_reg | 复位 |
|---|---|---|---|---|---|
| **L0** | 告警 | 仅提示，系统照常运行 | 无 | 置位 | 条件消失自动清 |
| **L1** | 限载 | 抑制工况恶化，不停机 | 禁升速/降容/冷凝风全开/关小 EXV | 置位 | 条件消失自动清 |
| **L2** | 延时停机 | 保护性停机，可重启 | 延时到点 → §5.4 故障停机 | **锁存** | 手动复位（coils 0x7） |
| **L3** | 锁定停机 | 反复故障，禁止自动重试 | 同 L2 + 置 `retry_lockout` | **锁存** | 手动复位 + 清重试计数 |

L1 是关键层：同一物理量按严重度设多级阈值，**先自保（限载/降容）再跳闸**。

### 6.2 动作码

| 码 | 名称 | 含义 | 状态 |
|---|---|---|---|
| 0 | `ACT_NONE` | 仅告警 | 已实现 |
| 1 | `ACT_SAFE_STOP` | 故障停机 §5.4 | 已实现 |
| 2 | `ACT_EXV_REDUCE` | 关小 EXV，不停机 | 已实现 |
| 3 | `ACT_INHIBIT_UP` | 禁升速：容量环只许降不许升 | 已实现 Step5b |
| 4 | `ACT_UNLOAD` | 降容：按 `UNLOAD_RATE_THROTTLE_S` 强制降压缩机油门 | 已实现 Step5b |
| 5 | `ACT_COND_FAN_MAX` | 冷凝风机强制全速排热 | 已实现 Step5b |
| 6 | `ACT_NORMAL_STOP` | 正常停机 §5.3（不锁存 error_reg） | 已实现 Step5b |

### 6.3 分级阈值表

同一物理量的多级阈值必须满足 **warn < unload < trip** 的单调关系，supervisor 启动时校验，违反则拒绝启动并置 `CONFIG_IO_FAULT`。

| 物理量 | L0 告警 | L1 限载 | L2/L3 跳闸 |
|---|---|---|---|
| 排气压力 | `HP_WARN` 19.0 bara | `HP_UNLOAD` 20.5 bara → 降容 + 冷凝风全开 | `HP_MAX` 22.0 bara → 停机 |
| 整机电流 | `I_WARN` 38.0 A | `I_LIMIT` 40.0 A → 禁升速；`I_UNLOAD` 42.0 A → 降容 | `I_MAX` 45.0 A → 停机 |
| 排气温度 | `TD_WARN` 95.0°C | `TD_UNLOAD` 105.0°C → 降容（不低于回油转速） | `TD_TRIP` 115.0°C → 持续 300s 停机 |
| 吸气压力 | `LP_WARN` 3.0 bara | `LP_UNLOAD` 2.8 bara → 降容 | `LP_MIN` 2.5 bara → 停机 |
| 过热度 | — | `SH_MIN` 2.0 K → 关小 EXV；`SH_CRIT` 1.0 K → 降容 | 不跳闸 |

> **L0 告警实现状态（Step6）**：`HP_WARN` / `LP_WARN` / `I_WARN` / `TD_WARN` 目前仅参与分级阈值单调性校验，**L0 告警规则暂作为预留扩展**（未在 `Alarm/rules.py` 生成动作），参数保留在 Modbus 点位表。L1/L2/L3 已完整实现。

### 6.4 延时槽设计

8 个共享延时槽：`delay_slots[0..7] = [0, 5, 10, 30, 45, 60, 300, 900]`（秒）

| 槽号 | 默认值 | 用途 |
|---|---|---|
| 0 | 0 | 即时动作 |
| 1 | 5 | 传感器 BAD / EXV 故障 / 风机故障 / **启停时序短延时** |
| 2 | 10 | 低压 / 吸气压力传感器 BAD / 流量故障 |
| 3 | 30 | 预留 |
| 4 | 45 | 预留 |
| 5 | 60 | Modbus 通讯断延时 |
| 6 | 300 | 排气温度二阶停机（5 min 持续高温） |
| 7 | 900 | 预留长延时 |

### 6.5 完整规则表（L1/L2 均已实现，共 22 条）

**L2 锁存/停机类（Step3 已实现）**：

| 规则 | error_reg 位 | 条件 | 延时槽 | 级 | 动作 |
|---|---|---|---|---|---|
| 高压保护 | 4 | 排气压力 > `HP_MAX` | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 排气压力传感器 BAD | 12 | quality BAD | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 低压保护 | 5 | 吸气压力 < `LP_MIN` | 槽 2 (10s) | L2 | 锁存 + §5.4；**仅 RUNNING** |
| 吸气压力传感器 BAD | 11 | quality BAD | 槽 2 (10s) | L2 | 锁存 + §5.4；**仅 RUNNING** |
| 低过热度 | 9 | SH < `SH_MIN` | 槽 1 (5s) | L1 | 关小 EXV，不锁存 |
| 过流 | 7 | 电流 > `I_MAX` | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 电流传感器 BAD | 13 | quality BAD | 槽 1 (5s) | L0/L2 | `CURRENT_BAD_ACTION=0` 仅告警（条件消失自动清）；`=1` 延时停机并锁存 |
| Driver 链路断 | 不占位 | link_ok 连续假 | 槽 1 (5s) | L2 | 锁存软标志 + 全 ESC 禁用 → 1x100 |
| EXV 故障 | 0 | `ST_VALVE_FAULT` 事件位 | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 传感器全超龄 | 14 | 全 10 通道最小年龄 > `AGE_LIMIT_MS` | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 配置/阈值校验失败 | 10 | 分级阈值单调性违反 / config.json 读写失败 | — | L2 | 拒绝启动并置位 |

**L1 限载规则（Step5b 实现）**：

| 规则 | 条件 | 延时槽 | 动作 | 锁存 |
|---|---|---|---|---|
| 高压限载 | 排气压力 > `HP_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_COND_FAN_MAX` | 否 |
| 电流禁升 | 电流 > `I_LIMIT` | 槽 1 (5s) | `ACT_INHIBIT_UP` | 否 |
| 电流降容 | 电流 > `I_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 低压限载 | 吸气压力 < `LP_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 临界低过热 | SH < `SH_CRIT` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_EXV_REDUCE` | 否 |
| 排气温度降容 | 排气温度 > `TD_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 排气温度停机 | 排气温度 > `TD_TRIP` 连续 | 槽 6 (300s) | `ACT_SAFE_STOP` 锁存（位 6 `HIGH_DISCHARGE_TEMP`） | 是 |

**L2 新增规则（Step5a/5b）**：

| 规则 | error_reg 位 | 条件 | 延时槽 | 归属 |
|---|---|---|---|---|
| 压缩机故障 | 1 | 使能且有油门指令，但反馈转速 `ST_ESC0_RPM` ≈0（ESC 保护或电机异常） | 槽 1 (5s) | Step5a |
| 蒸发风机故障 | 2 | 使能且有油门指令，但反馈转速 `ST_ESC1_RPM` 偏离目标 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a |
| 冷凝风机故障 | 3 | 使能且有油门指令，但反馈转速 `ST_ESC2_RPM` 偏离目标 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a |
| Modbus 通讯断 | 8（**不置位**，bit=None） | 无有效 Modbus 帧超 `MODBUS_TIMEOUT_S` | 槽 5 (60s) | Step5b |
| 流量故障（预留） | 15 | 后续带水回路版本：实体 DI 断开（`FLOW_ENABLE=1` 时生效）；当前空冷直膨样机的 DI 是软件占位，不构成实物保护 | 槽 2 (10s) | 仅规则代码可模拟测试，实体功能待水回路版本接入 |

> **Modbus 通讯断说明**：按决策 Q4 走正常停机（`ACT_NORMAL_STOP`）不锁存 error_reg，位 8 为预留。副作用：停机后触摸屏上看不到停机原因指示，如需可见性可后续拍板改为置位不锁存。

### 6.6 重试锁定（L3）

同一故障在 `RETRY_WINDOW_S`（默认 3600s）内锁存达 `RETRY_MAX`（默认 3）次 → 升级 L3，置 `retry_lockout`，禁止自动重启。

**报警条件消失即自动清 `retry_lockout`**，但 error_reg 锁存位仍需手动复位。

### 6.7 防短循环

| 参数 | 默认 | 含义 |
|---|---|---|
| `MIN_OFF_S` | 300 | 压缩机停机后最短再启动间隔 |
| `MIN_ON_S` | 180 | 压缩机启动后最短运行时间 |
| `MAX_STARTS_PER_HOUR` | 6 | 每小时最大启动次数 |

**铁律**：`MIN_ON_S` 只约束温控停机，**绝不阻塞 L2/L3 保护停机**。

---

## 7. 系统主逻辑（三环控制）

### 7.1 RUNNING 子状态

```python
RUNNING_PULLDOWN   = 0  # R1: 降温中，容量环全力
RUNNING_MODULATING = 1  # R2: 接近设定，容量环调节
RUNNING_LIMITED    = 2  # R3: L1 限载中
RUNNING_THERMO_OFF = 3  # R4: 达到设定，压缩机停
```

### 7.2 容量环（蒸发器出口空气温度 → 压缩机油门）

**控制架构（v3.3 修订）**：

```
外环（容量环，10s）：蒸发器出口空气温度 → throttle_base（基础油门，绝对值）
                         ↓
转速环（可选，0.5s）：throttle_base 映射 rpm_cmd → RPM 反馈 PID → 最终油门
                         ↓
             throttle_final（PID 调节并限幅）→ ESC0
                         ↑
                    ESC0 遥测反馈 rpm
```

#### 7.2.1 外环：容量环（温度 PID）

**输入**：`sensor_checked[IDX_OUTLET_T]`（蒸发器出口空气温度）  
**输出**：`comp_throttle_base`（压缩机基础油门，绝对值 48-2047）  
**目标**：`compressor_control_params["setpoint"] = 22.0°C`（Modbus 4x19 可调，×100 定点）  
**周期**：10s

**PID 参数**（`compressor_control_params`）：

```python
{
    'Kp': 50.0,             # 比例系数：温度偏差 1K → 油门变化 50
    'Ki': 5.0,              # 积分系数
    'Kd': 10.0,             # 微分系数
    'setpoint': 22.0,       # 蒸发器出口空气温度目标 °C
    'deadband': 0.5,        # 死区 ±0.5K
    'throttle_min': 48,     # 最小油门（可调参数）
    'throttle_max': 2047,   # 最大油门（可调参数）
    'max_delta': 200,       # 单次最大变化 200 throttle/10s
}
```

**控制流程**：
1. 读取蒸发器出口空气温度实时值，检查 quality 与 age
2. 计算偏差：`error = setpoint - 实时值`
3. PID 运算输出油门变化量（基于启动默认油门累加）
4. L1 限载覆盖（P2 优先级）：
   - `ACT_INHIBIT_UP`：禁止油门继续增加
   - `ACT_UNLOAD`：按 `UNLOAD_RATE_THROTTLE_S` 强制降到 `COMP_OIL_RETURN_THROTTLE`
5. 限幅：`[throttle_min, throttle_max]`
6. 斜坡限速：升速/降速速率受 `max_delta` 限制
7. 输出：`comp_throttle_base`（油门绝对值）

**质量门禁**：quality 非 OK 或 age > 2s → 保持当前 throttle_base，冻结积分。

#### 7.2.2 转速环：按基础油门映射目标转速的反馈调节（可选）

**输入**：`comp_throttle_base`（基础油门）+ `state_data[ST_ESC0_RPM]`（反馈转速）  
**目标转速**：`rpm_cmd = comp_throttle_base / ESC_THROTTLE_MAX * ST_MOTOR_SPEED_MAX`  
**输出**：转速 PID 累计调节后的 ESC 油门；单次调节受 `throttle_max_adjust` 限制  
**周期**：0.5s（持续运行）
**使能开关**：`COMP_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`comp_speed_loop_params`）：

```python
{
    'Kp': 0.5,                  # throttle/rpm（待台架标定）
    'Ki': 0.1,                  # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 6000,            # throttle 映射的满量程目标转速
    'deadband': 200,            # ±200 rpm 死区
    'throttle_max_adjust': 100, # PID 单次最大油门调节量
}
```

**控制流程**：
1. 检查 `COMP_SPEED_LOOP_ENABLE`；禁用时跳过转速 PID，基础油门经 ESC 边界限幅后开环输出
2. 将 `comp_throttle_base` 映射为目标转速 `rpm_cmd`（基础油门满量程对应 `rpm_max`）
3. 读取 `state_data[ST_ESC0_RPM]`（ESC 遥测反馈），以 `rpm_cmd` 为设定值运行增量式 PID
4. 将 PID 调节量累计到当前油门；单次变化受 `throttle_max_adjust` 限制
5. 最终油门限制到 `[ESC_THROTTLE_MIN, ESC_THROTTLE_MAX]`；停机时强制 0
6. 发送给 Driver ESC0

**质量门禁**：反馈 rpm 无效（999）时本周期不更新转速 PID；Driver 链路断时设备禁用并清零控制器状态。

**实现位置**：`Compressor/control.py`；公共接口由 `Compressor/__init__.py` 导出（Step5b 实现）

> **MINOR-1（Step6 已修复）**：`COMP_SPEED_LOOP_ENABLE` 现由 `Compressor()` 显式消费——禁用时跳过转速 PID，开环直接下发容量环油门（仅限幅）；冷凝/蒸发风机同理（`COND_FAN_SPEED_LOOP_ENABLE` / `EVAP_FAN_SPEED_LOOP_ENABLE`）。

### 7.3 冷凝压力环（排气压力 → 冷凝风机油门）

**控制架构（当前实现口径）**：

```
外环（压力环，2s）：排气压力 → throttle_base（基础油门，绝对值）
                       ↓
转速环（可选，0.5s）：throttle_base 映射 rpm_cmd → RPM 反馈 PID → 最终油门
                       ↓
                throttle_final（PID 调节并限幅）→ ESC2
                       ↑
                  ESC2 遥测反馈 rpm
```

#### 7.3.1 外环：冷凝压力环（压力 PID）

**输入**：`sensor_checked[IDX_DISCHARGE_PSI]`（排气压力，bara）  
**输出**：`cond_fan_throttle_base`（冷凝风机基础油门，绝对值 48-2047）  
**目标**：`HP_TARGET = 12.0 bara`  
**周期**：2s

**PID 参数**（`cond_loop_params`）：

```python
{
    'HP_TARGET': 12.0,             # bara
    'Kp': 100.0,                   # 比例系数：压力偏差 1 bara → 油门变化 100
    'Ki': 10.0,                    # 积分系数
    'Kd': 20.0,                    # 微分系数
    'COND_LOOP_PERIOD_S': 2.0,
    'throttle_min': 200,           # 最小油门（压缩机运行期间必须排热）
    'throttle_max': 2047,          # 最大油门
    'max_delta': 150,              # 单次最大变化 150 throttle/2s
}
```

**控制流程**：
1. 读取排气压力实时值，检查 quality 与 age
2. 计算偏差：`error = 实时值 - HP_TARGET`
3. PID 运算输出油门变化量（基于启动默认油门累加）
4. L1 限载覆盖：`ACT_COND_FAN_MAX` → 强制全速（throttle_max）
5. 限幅：`[throttle_min, throttle_max]`
6. 输出：`cond_fan_throttle_base`（油门绝对值）

**质量门禁**：quality 非 OK 或 age > 2s → 保持当前 throttle_base，冻结积分。

#### 7.3.2 转速环：按基础油门映射目标转速的反馈调节（可选）

**输入**：`cond_fan_throttle_base`（基础油门）+ `state_data[ST_ESC2_RPM]`（反馈转速）  
**目标转速**：`rpm_cmd = cond_fan_throttle_base / ESC_THROTTLE_MAX * ST_FAN_SPEED_MAX`  
**输出**：转速 PID 累计调节后的 ESC 油门；单次调节受 `throttle_max_adjust` 限制  
**周期**：0.5s（持续运行）
**使能开关**：`COND_FAN_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`fan_speed_loop_params`）：

```python
{
    'Kp': 0.3,                  # throttle/rpm（待台架标定）
    'Ki': 0.05,                 # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 3000,            # throttle 映射的满量程目标转速
    'deadband': 200,            # ±200 rpm 死区
    'throttle_max_adjust': 100, # PID 单次最大油门调节量
}
```

**控制流程**：
1. 检查 `COND_FAN_SPEED_LOOP_ENABLE`；禁用时跳过转速 PID，基础油门经 ESC 边界限幅后开环输出
2. 将 `cond_fan_throttle_base` 映射为目标转速 `rpm_cmd`（基础油门满量程对应 `rpm_max`）
3. 读取 `state_data[ST_ESC2_RPM]`（ESC 遥测反馈），以 `rpm_cmd` 为设定值运行增量式 PID
4. 将 PID 调节量累计到当前油门；单次变化受 `throttle_max_adjust` 限制
5. 最终油门限制到 `[ESC_THROTTLE_MIN, ESC_THROTTLE_MAX]`；停机时强制 0
6. 发送给 Driver ESC2

**质量门禁**：反馈 rpm 无效（999）时本周期不更新转速 PID；Driver 链路断时设备禁用并清零控制器状态。

**实现位置**：`Condenser/control.py`；公共接口由 `Condenser/__init__.py` 导出（Step5b 实现）

### 7.4 过热度环（过热度 → EXV 开度）

**输入**：`sensor_checked[IDX_SUPERHEAT]`（过热度，K）  
**输出**：EXV 开度增量  
**目标**：`control_params["setpoint"] = 5.0 K`（Modbus 4x4 可调，×10 定点）  
**周期**：1s

**PID 参数**（`control_params`）：

```python
{
    'Kp': 0.6,
    'Ki': 0.12,
    'Kd': 3.0,
    'setpoint': 5.0,
    'error_threshold': 2.0,
    'deadband': 0.2,
    'max_delta': 8,
}
```

**已实现**：`EXV/Valve_CRTL.py`，质量门禁已接入。

### 7.5 蒸发风机（用户手动设定 + 可选转速反馈调节）

**控制架构（当前实现口径）**：

```
用户设定（hr[221]，即 4x222）→ throttle_base（基础油门，绝对值）
                       ↓
转速环（可选，0.5s）：throttle_base 映射 rpm_cmd → RPM 反馈 PID → 最终油门
                       ↓
                throttle_final（PID 调节并限幅）→ ESC1
                       ↑
                  ESC1 遥测反馈 rpm
```

**策略**：RUNNING 期间始终读取 `evap_fan_user_params["EVAP_FAN_USER_THROTTLE"]`（Modbus hr[221]，即 4x222）作为基础油门；转速环按 `EVAP_FAN_SPEED_LOOP_ENABLE` 决定是否根据 ESC1 RPM 反馈闭环调节油门。

**实现**：`Evaporator/control.py`；公共接口由 `Evaporator/__init__.py` 导出（Step5b 实现）

#### 7.5.1 基础油门来源

- **STARTUP**：服从 `supervisor_interlocks["evap_fan_throttle_cmd"]`（固定值 `EVAP_FAN_START_THROTTLE`）
- **RUNNING**：读取 hr[221]（4x222）用户设定值（默认 1500，油门绝对值）

#### 7.5.2 转速环：按基础油门映射目标转速的反馈调节（可选）

**输入**：`evap_fan_throttle_base`（基础油门）+ `state_data[ST_ESC1_RPM]`（反馈转速）  
**目标转速**：基础油门按 `ST_FAN_SPEED_MAX` 映射得到 `rpm_cmd`  
**输出**：转速 PID 累计调节后的 ESC 油门；单次调节受 `throttle_max_adjust` 限制  
**周期**：0.5s（持续运行）
**使能开关**：`EVAP_FAN_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`fan_speed_loop_params`，与冷凝风机共享）：

```python
{
    'Kp': 0.3,                  # throttle/rpm（待台架标定）
    'Ki': 0.05,                 # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 3000,            # throttle 映射的满量程目标转速
    'deadband': 200,            # ±200 rpm 死区
    'throttle_max_adjust': 100, # PID 单次最大油门调节量
}
```

**控制流程**：
1. 检查 `EVAP_FAN_SPEED_LOOP_ENABLE`；禁用时跳过转速 PID，基础油门经 ESC 边界限幅后开环输出
2. 将基础油门映射为目标转速 `rpm_cmd`（基础油门满量程对应 `rpm_max`）
3. 读取 `state_data[ST_ESC1_RPM]`（ESC 遥测反馈），以 `rpm_cmd` 为设定值运行增量式 PID
4. 将 PID 调节量累计到当前油门；单次变化受 `throttle_max_adjust` 限制
5. 最终油门限制到 `[ESC_THROTTLE_MIN, ESC_THROTTLE_MAX]`；停机时强制 0
6. 发送给 Driver ESC1

**质量门禁**：反馈 rpm 无效（999）时本周期不更新转速 PID；Driver 链路断时设备禁用并清零控制器状态。

### 7.6 温控循环（R2 ↔ R4）

**R1/R2 → R4**：达到设定且最短运行时间满足时：
- 条件：蒸发器出口空气温度 <= setpoint 且压缩机转速 <= `ST_MOTOR_SPEED_MIN`，且 `MIN_ON_S` = 180s 已满
- 动作（`_apply_thermo_off_interlocks()`）：进入 R4 THERMO_OFF
  - 压缩机：`comp_enable=False`，`comp_throttle_cmd=0`
  - 冷凝风机：停（`cond_fan_enable=False`）
  - EXV：关至 **10%**（不是完全归零——避免再次启动时开度突变）
  - 蒸发风机：可选保持（`EVAP_FAN_THERMO_OFF_KEEP`，默认 1=保持）
- R4 期间外环 `comp_throttle_base` / `cond_fan_throttle_base` 不下发联锁，保持进入 R4 前的最后值

**R4 → R1（温控重启，路径已明确）**：回温超回差且最短停机时间满足时：
- 条件：蒸发器出口空气温度 > (setpoint + `THERMO_HYST`)，且 `MIN_OFF_S` = 300s 已满
- 动作：**RUNNING 内直接再启动，不走 S1-S6 启动时序**——
  `running_substate = RUNNING_PULLDOWN`，下一拍 `comp_enable=True` 并**沿用进入 R4 前的 `comp_throttle_base`**；
  不经 `COMP_START_THROTTLE` 恒定油门启动、无 EXV→压缩机延时、无转速稳定性晋级判据
- 设计依据：`MIN_ON_S`/`MIN_OFF_S` 保证压缩机不短循环，300s 停机使高低压差部分平衡、油回流；
  直接重启免去全时序等待，温控循环响应快
- ⚠️ 风险与缓释：沿用的旧油门在降温段可能较高，重启瞬间负载冲击大于冷启动；
  且 R4 期间 EXV 保持 10%，重启瞬间吸气量小。若目标机实测出现启动冲击或液击迹象，
  改为经 `COMP_START_THROTTLE` 软重启（小改 `_update_running_substate` 即可）

**质量门禁**：蒸发器出口空气温度 quality 非 OK 或 age > 2s → 子状态冻结，不做任何转换。

---

## 8. 手动控制模式

### 8.1 手动模式参数（v3.3 修订，地址按 2026-09-19 四区重构）

| 地址 | 名称 | 类型 | 说明 | 默认 |
|---|---|---|---|---|
| 0x2（coils[1]） | `MANUAL_MODE_ENABLE` | bit | 0=自动 / 1=手动（控制压缩机/冷凝风机/EXV） | 0 |
| 4x220（hr[219]） | `MANUAL_COMP_THROTTLE` | u16 | 手动压缩机油门 0 或 48-2047 | 1000 |
| 4x221（hr[220]） | `MANUAL_COND_FAN_THROTTLE` | u16 | 手动冷凝风机油门 0 或 48-2047 | 1000 |
| 4x222（hr[221]） | `EVAP_FAN_USER_THROTTLE` | u16 | 蒸发风机油门 48-2047（RUNNING 期间始终生效） | 1500 |
| 4x223（hr[222]） | `MANUAL_EXV_PCT` | u16 | 手动 EXV 开度 0-1000（0.1 倍率） | 300 (30%) |

> **地址沿革**：Step5a 旧映射 hr9/hr11-14 → Step6 映射 hr10/hr12-15 →
> **2026-09-19 四区重构后**为上表（coils 0x2 + 4x220-223），以
> `MODBUS_POINT_TABLE.md`（v4.1）/ `MODBUS_ADDRESS_MAP.md` 为准。

**设计变更（v3.3）**：
- 4x220/221/222 为**油门绝对值（throttle）**，范围 48-2047
- 手动模式下，转速环是否工作由对应开关决定；启用时对手动基础油门对应的目标转速闭环调节，禁用时开环输出。该开关有意保留给操作者，不因手动模式而强制开或关
- 转速环启用时，手动模式与自动模式使用相同的 RPM 反馈调节机制，区别仅在基础油门来源

**说明**：
- 0x2=1 时，压缩机、冷凝风机、EXV 受 4x220/4x221/4x223 控制，容量环和冷凝压力环输出无效
- 4x222 蒸发风机在 RUNNING 期间**始终生效**，不受 0x2 控制
- 手动模式仍受保护逻辑约束（高压/过流等仍触发停机）
- 手动模式下按转速环开关选择开环或闭环；开启时执行转速反馈约束，关闭时不提供转速反馈约束

### 8.2 手动模式安全约束

- 手动油门经设备模块限幅在 `[48, 2047]`（停机为 0）
- **转速约束依赖对应转速环开关**：手动模式下开关仍由操作者控制。开启时，手动基础油门映射为目标转速并由反馈环调节；关闭时手动油门开环直发，不提供转速反馈约束。保留该开关是为了允许现场按需要选择开环/闭环，手动模式不强制切换开关状态。
- supervisor 禁用时（`comp_enable=False`），手动指令无效

---

## 9. 参数体系

### 9.1 参数分类

| 类别 | 参数示例 | Modbus 可读写 | 掉电保存 | 修改生效时机 |
|---|---|---|---|---|
| **保护阈值** | `HP_MAX`, `LP_MIN`, `I_MAX` | ✅ | ✅ | 立即（下一周期判定） |
| **延时槽** | `delay_slots[8]` | ✅ | ✅ | 立即（影响正在计时的规则） |
| **控制环参数** | 容量环 Kp/Ki/Kd, `setpoint` | ✅ | ✅ | 立即（PID 同步） |
| **启动时序参数** | `COMP_START_THROTTLE`, `EXV_START_POS` | ✅ | ✅ | 下次启动生效 |
| **限速参数** | `ST_MOTOR_SPEED_MIN/MAX`, `ST_FAN_SPEED_MIN/MAX` | ✅ | ❌（映射 state_data，不在持久化组） | 立即（影响输出限幅） |
| **ESC 倍率** | `ESC_TELEMETRY_CFG` | ❌（编译期常量） | ❌ | — |
| **手动指令** | `MANUAL_COMP_THROTTLE`, `MANUAL_COND_FAN_THROTTLE`, `EVAP_FAN_USER_THROTTLE` | ✅ | ❌ | 立即（手动模式下） |
| **软件版本** | `SW_VERSION` | ❌ 只读 | — | — |

### 9.2 默认值表（完整，v3.3 更新）

```python
# 保护阈值（绝压 bara，×100）
protect_params = {
    # 分级阈值（多级保护）
    "HP_MAX": 22.0,              # bara，L2 停机
    "HP_UNLOAD": 20.5,           # bara，L1 降容
    "HP_WARN": 19.0,             # bara，L0 告警
    "HP_TARGET": 12.0,           # bara，冷凝压力环目标
    
    "LP_MIN": 2.5,               # bara，L2 停机
    "LP_UNLOAD": 2.8,            # bara，L1 降容
    "LP_WARN": 3.0,              # bara，L0 告警
    "LP_HYST": 0.3,              # bara，回升迟滞
    
    "I_MAX": 45.0,               # A，L2 停机
    "I_UNLOAD": 42.0,            # A，L1 降容
    "I_LIMIT": 40.0,             # A，L1 禁升速
    "I_WARN": 38.0,              # A，L0 告警
    
    "TD_TRIP": 115.0,            # °C，L2 连续 300s 停机
    "TD_UNLOAD": 105.0,          # °C，L1 降容
    "TD_WARN": 95.0,             # °C，L0 告警
    
    "SH_MIN": 2.0,               # K，L1 关小 EXV
    "SH_CRIT": 1.0,              # K，L1 降容
    
    # 延时槽（秒）
    "delay_slots": [0, 5, 10, 30, 45, 60, 300, 900],
    
    # 启动时序（v3.3 改为油门）
    "EXV_START_POS": 30.0,                    # %
    "COMP_START_THROTTLE": 1200,             # 压缩机启动油门（恒定值，48-2047）
    "COND_FAN_START_THROTTLE": 1000,         # 冷凝风机启动油门
    "EVAP_FAN_START_THROTTLE": 800,          # 蒸发风机启动油门
    "EXV_TO_COMP_DELAY_SLOT": 1,             # EXV→压缩机延时槽号
    "COMP_STOP_DELAY_SLOT": 1,               # 停机压缩机延时槽号
    "FAN_STOP_DELAY_SLOT": 1,                # 停机风机延时槽号
    
    # 晋级参数（v3.3 改为转速稳定性判据）
    "STARTUP_ADVANCE_MODE": 1,               # 0=定速保持，1=转速稳定性检查
    "COMP_HOLD_S": 10.0,                     # 定速保持时间（模式 0）
    "COMP_START_DELAY_S": 15.0,              # 启动后延时判定窗口起始时间（模式 1）
    "COMP_RPM_STABILITY_WINDOW_S": 10.0,     # 转速稳定性检查窗口（10秒）
    "COMP_RPM_STABILITY_THRESHOLD": 100,     # 转速变化阈值 ±100 rpm（窗口内变化小于此值视为稳定）
    "COMP_RPM_STABILITY_SAMPLE_S": 1.0,      # 采样间隔 1秒
    
    # 防短循环
    "MIN_ON_S": 180.0,                       # 最短运行时间
    "MIN_OFF_S": 300.0,                      # 最短停机间隔
    "MAX_STARTS_PER_HOUR": 6,                # 每小时最大启动次数
    
    # 降容/回油（v3.3 改为油门）
    "COMP_OIL_RETURN_THROTTLE": 200,         # 回油最低油门
    "UNLOAD_RATE_THROTTLE_S": 50,            # 降容速率 throttle/s
    
    # 转速环开关（v3.3 新增）
    "COMP_SPEED_LOOP_ENABLE": 1,             # 压缩机转速环使能（0=禁用，1=启用）
    "COND_FAN_SPEED_LOOP_ENABLE": 1,         # 冷凝风机转速环使能（0=禁用，1=启用）
    "EVAP_FAN_SPEED_LOOP_ENABLE": 1,         # 蒸发风机转速环使能（0=禁用，1=启用）
    
    # 重试锁定
    "RETRY_MAX": 3,                          # 最大重试次数
    "RETRY_WINDOW_S": 3600,                  # 重试窗口（1 小时）
    
    # 通讯与流量
    "MODBUS_TIMEOUT_S": 60.0,                # Modbus 无帧超时
    "FLOW_ENABLE": 0,                        # 后续带水回路版本流量保护；当前空冷直膨样机无水回路，保持禁用
    "CURRENT_BAD_ACTION": 0,                 # 电流传感器 BAD：0=仅告警（条件消失自动清），1=延时停机并锁存
    "LINK_CHECK_ENABLE": 0,                  # 链路检测使能
}

# 容量环 PID（外环，温度→油门）v3.3 修订
compressor_control_params = {
    'Kp': 50.0,                    # 比例系数：温度偏差 1K → 油门变化 50
    'Ki': 5.0,                     # 积分系数
    'Kd': 10.0,                    # 微分系数
    'setpoint': 22.0,              # 蒸发器出口空气温度目标 °C
    'deadband': 0.5,               # 死区 ±0.5K
    'throttle_min': 48,            # 最小油门（可调参数）
    'throttle_max': 2047,          # 最大油门（可调参数）
    'max_delta': 200,              # 单次最大变化 200 throttle/10s
}

# 压缩机转速环 PID（基础油门映射目标转速→RPM 反馈调节油门）
comp_speed_loop_params = {
    'Kp': 0.5,                     # throttle/rpm（待台架标定）
    'Ki': 0.1,                     # 积分系数
    'Kd': 0.0,                     # 微分系数
    'period_s': 0.5,               # 0.5s 周期
    'rpm_max': 6000,               # throttle 满量程映射的目标转速
    'deadband': 200,               # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100,    # PID 单次最大油门调节量
}

# 冷凝压力环 PID（外环，压力→油门）v3.3 修订
cond_loop_params = {
    'HP_TARGET': 12.0,             # bara
    'Kp': 100.0,                   # 比例系数：压力偏差 1 bara → 油门变化 100
    'Ki': 10.0,                    # 积分系数
    'Kd': 20.0,                    # 微分系数
    'COND_LOOP_PERIOD_S': 2.0,
    'throttle_min': 200,           # 最小油门（压缩机运行期间必须排热）
    'throttle_max': 2047,          # 最大油门
    'max_delta': 150,              # 单次最大变化 150 throttle/2s
}

# 风机转速环 PID（基础油门映射目标转速→RPM 反馈调节油门）
fan_speed_loop_params = {
    'Kp': 0.3,                     # throttle/rpm（待台架标定）
    'Ki': 0.05,                    # 积分系数
    'Kd': 0.0,                     # 微分系数
    'period_s': 0.5,               # 0.5s 周期
    'rpm_max': 3000,               # throttle 满量程映射的目标转速
    'deadband': 200,               # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100,    # PID 单次最大油门调节量
}

# 手动模式（油门设定；转速环开关独立由操作者控制）
manual_params = {
    'MANUAL_MODE_ENABLE': 0,           # 0=自动 / 1=手动
    'MANUAL_COMP_THROTTLE': 1000,      # 手动压缩机油门 0 或 48-2047
    'MANUAL_COND_FAN_THROTTLE': 1000,  # 手动冷凝风机油门 0 或 48-2047
    'MANUAL_EXV_PCT': 30.0,            # 手动 EXV 开度 0.0-100.0%
}

evap_fan_user_params = {
    'EVAP_FAN_USER_THROTTLE': 1500,    # 蒸发风机基础油门（RUNNING 期间生效）
}
```

### 9.3 ESC 遥测配置

```python
# ESC 遥测配置
ESC_TELEMETRY_CFG = [
    # ESC0 压缩机
    {"erpm": True,  "temp": True,  "voltage": True,  "current": True,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 3},
    # ESC1 蒸发风机 — 本机 current 无效
    {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
     "erpm_scale": 1.0, "voltage_scale": 2.1, "current_scale": 1.0, "pole_pairs": 7},
    # ESC2 冷凝风机 — 本机 current 无效
    {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
     "erpm_scale": 1.0, "voltage_scale": 2.1, "current_scale": 1.0, "pole_pairs": 7},
    # ESC3 备用
    {"erpm": True,  "temp": True,  "voltage": True,  "current": True,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 7},
]
```

**配置说明**：ESC0 与备用 ESC3 当前使用 `pole_pairs=3/7`、电压倍率 1.0；ESC1/ESC2 的电压倍率为 2.1，电流通道无效。倍率和极对数以 `Board/state.py` 的 `ESC_TELEMETRY_CFG` 为运行时配置真相；台架标定后如有变更，应同步更新本表。

### 9.4 参数持久化规范（✅ Step6 已实现，见 `Board/params_store.py`）

> 实现状态（2026-09-15）：`Board/params_store.py` 已落地单一 `config.json`
> （version/checksum/原子写 tmp+rename/30s 防抖/SAFE_STOP 立即保存/损坏回退默认值/版本迁移钩子）；
> `main.py` 启动时 `load_config()`，Modbus 写入 `mark_dirty()`，`supervisor._enter_safe_stop()`
> 调用 `save_if_dirty()`。掉电保持已在 PC 端模拟验证（test_step6）。

#### 9.4.1 持久化范围

**实际持久化（`Board/params_store.py` `_PERSIST_GROUPS`，6 组参数字典）**：

| 组 | 内容 |
|---|---|
| `sh_pid` | 过热度环 `control_params` |
| `cap_pid` | 容量环 `compressor_control_params` |
| `cond_pid` | 冷凝压力环 `cond_control_params` |
| `comp_speed_loop` | 压缩机转速环参数 |
| `fan_speed_loop` | 风机转速环参数 |
| `protect` | `protect_params` 全键（保护阈值、延时槽、启动时序、防短循环、重试、转速环开关、通讯/流量等） |

**不持久化**：
- 手动模式指令（0x2、4x220-4x223）：运行时临时指令
- 故障复位（coils 0x7）：脉冲命令
- 限速参数 `ST_MOTOR_SPEED_MIN/MAX`、`ST_FAN_SPEED_MIN/MAX`（映射 state_data，不在 6 组内——
  掉电后回默认值，如需保持请拍板是否纳入持久化）
- 所有只读寄存器（Input Registers）与运行时状态变量

#### 9.4.2 存储格式

**文件**：`config.json`（单一配置文件）

**格式**：
```json
{
  "version": 1,
  "timestamp": 1234567890,
  "checksum": "0xABCD1234",
  "params": {
    "sh_pid": {"Kp": 0.6, "Ki": 0.12, "Kd": 3.0, ...},
    "cap_pid": {"Kp": 50.0, "Ki": 5.0, ...},
    "cond_pid": {...},
    "comp_speed_loop": {...},
    "fan_speed_loop": {...},
    "protect": {"HP_MAX": 22.0, "LP_MIN": 2.5, ...},
    "delay_slots": [0, 5, 10, 30, 45, 60, 300, 900],
    "startup": {...},
    "speed_loop_enable": {...}
  }
}
```

#### 9.4.3 写入触发机制

**触发条件**：
1. **参数修改防抖**：Modbus 写入后延时 30 秒再保存（避免频繁写 flash）
2. **正常停机时**：系统进入 SAFE_STOP 时保存一次
3. **手动触发**：Modbus 写特殊命令触发立即保存（可选）

**防抖算法**：
```python
# 伪代码
on_modbus_write(hr_addr, value):
    update_ram_params(hr_addr, value)
    reset_save_timer(30_seconds)  # 重置计时器
    
on_save_timer_expire():
    save_to_flash()
    
on_safe_stop_enter():
    cancel_save_timer()
    save_to_flash()  # 立即保存
```

#### 9.4.4 Flash 写入保护

**Flash 寿命管理**：
- STM32H743 Flash 最小擦写次数：10,000 次/块
- 保守估算：单块可用 1,000 次
- 防抖 30 秒 + 日常调参频率 → 预估寿命 > 10 年

**写入策略**：
- 采用 JSON 格式（便于调试和版本迁移）
- 单文件覆盖写入（简单可靠）
- 写入前先写临时文件 `config.json.tmp`，成功后重命名（原子性）
- 写入失败时保留旧配置

**损坏检测与回退**：
```python
def load_config():
    try:
        with open('config.json', 'r') as f:
            cfg = json.load(f)
        if verify_checksum(cfg):
            return cfg
        else:
            log_error("Config checksum mismatch")
            return load_defaults()
    except:
        log_error("Config load failed, using defaults")
        return load_defaults()
```

#### 9.4.5 版本迁移

**版本号管理**：
- 配置文件包含 `"version": 1`
- 代码中定义 `CONFIG_VERSION_CURRENT = 1`

**迁移策略**：
```python
def migrate_config(cfg):
    cfg_ver = cfg.get("version", 0)
    
    if cfg_ver < 1:
        # v0 → v1: 添加转速环参数
        cfg["speed_loop_enable"] = {"comp": 1, "cond": 1, "evap": 1}
        cfg["version"] = 1
    
    # 未来版本迁移在此添加
    # if cfg_ver < 2:
    #     ...
    
    return cfg
```

**兼容性原则**：
- 新增参数：提供默认值
- 删除参数：忽略（不报错）
- 重命名参数：兼容层映射旧名称
- 不向后兼容的变更：递增 major version

#### 9.4.6 MicroPython 兼容性

**JSON 限制**：
- MicroPython `json` 模块**不支持** `encoding=` / `indent=` / `ensure_ascii=` 参数
- 读写时不使用这些参数

**正确用法**：
```python
# ✅ 正确
with open('config.json', 'w') as f:
    json.dump(cfg, f)

# ❌ 错误（MicroPython 不支持）
with open('config.json', 'w') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
```

**PID_Plus 修复**（A6 问题）：
- 当前 `PID_Plus.save()` / `load()` 使用了不支持的参数
- Step6 修改为兼容的调用方式

#### 9.4.7 掉电测试标准

**Step6 验收标准**：
1. **基础功能**：
   - 修改任意 PID 参数 → 等待 30 秒 → 掉电重启 → 参数保持
   - 修改保护阈值 → SAFE_STOP → 掉电重启 → 参数保持
   
2. **防抖验证**：
   - 连续修改 10 个参数（间隔 < 30 秒）→ 只触发 1 次 flash 写入
   
3. **损坏恢复**：
   - 手动破坏 `config.json`（删除或改为非法 JSON）→ 重启 → 加载默认值，系统正常运行
   
4. **Flash 写入次数**：
   - 记录写入次数（日志或计数器）
   - 验收标准：< 10 次/小时（正常调参场景）

---

## 10. Modbus 接口

> **2026-09-19 重构（已实施）**：寄存器层从 Step6 旧平面模型（hr0-93/ir0-49/di0-31）迁移到
> **标准 Modbus 四区数据模型**（coils/di/hr/ir），改造计划原文见附录 B。
> **权威点表**：`MODBUS_POINT_TABLE.md`（v4.1）+ `MODBUS_POINT_TABLE.csv`（机器可读）+
> `MODBUS_ADDRESS_MAP.md`（地址换算与写入权限详述）。本节只给结构性摘要，点位细节以权威点表为准。

### 10.1 连接与地址约定

- UART8，RS485，9600 8N1，从机地址 1（可配置）
- **报文地址 = 代码索引**（0 基，标准 Modbus）；**屏上显示地址 = 代码索引 + 1**（如 4x1 ↔ `hr[0]`）
- 功能码：01/02/03/04 读，05/06 写单个，0F/10 写多个；广播写同样走范围校验

### 10.2 四区布局

**Coils 0x1-0x8（FC01/05/0F，可读写）**：系统使能 / 手动模式 / 三个转速环使能 / 流量保护使能（仅后续带水回路版本；当前样机须保持 0）/ 故障复位（写 1 脉冲、自动清零、回读恒 0）/ 晋级模式切换。

**Discrete Inputs 1x（FC02，只读）**：
- 1x1-1x16：`error_reg` 低 16 位按位镜像（1=正常 0=故障）
- 1x100-1x105：Driver 链路状态 / 四路联锁使能 / 重试锁定
> 1x16 `FLOW_FAULT` 在当前样机为预留状态；`di_flow` 尚非实体输入，不可据此判断实际水流。

**Holding Registers 4x1-4x223（FC03/06/10，可读写，稀疏定义）**：
- 写入走白名单校验：未定义地址 → 异常 0x02，越界值 → 0x03（多寄存器写入原子拒绝）
- 读保留地址返回 0（不异常），读越界（>4x223）→ 0x02
- 定义段：4x1-7（过热度 PID）、4x16-34（容量环/冷凝压力环）、4x50-62（保护阈值）、
  4x100-107（延时槽/启动时序）、4x120-123、4x130-132、4x150-157（转速环）、
  4x180-186（转速环/防短循环/重试/通讯超时）、4x200-205、4x220-223（手动指令，§8.1）

**Input Registers 3x1-3x223（FC04，只读）**：
- 3x1-3x8：入口/出口空气温度及设备温度×100 / 压力×100 / 过热度×10
- 3x9 / 3x10 / 3x11：过冷度×10 / 进口焓（吸气焓）×100 / 出口焓（液管焓）×100（v4.1 新增）
- 3x13-3x14：质量码（每通道 2 bit）；3x17：`sensor_fault_reg`（16-bit）
- 3x101-3x109：系统模式 / 子状态 / 执行机构状态 / 联锁 / 启动阶段 / 限载动作码
- 3x110 / 3x111：`error_reg` 低 16 位 / 高 16 位（bit16-31 仅经此通道暴露）
- 3x130-3x161：ESC 遥测（4 通道 × 4 字段）

### 10.3 维护规约

- `Display_Touch/modbus_regs.py` 与 `modbus_slave.py` 必须保持上表布局；改点位必须先改 `MODBUS_POINT_TABLE.csv` 再改代码
- 新增可写参数：在 `_HR_RANGE` 白名单登记范围，并确认是否纳入 `params_store` 持久化组（§9.4.1）

---

## 11. 已拍板决策记录

### 11.1 架构决策

| # | 决策 | 日期 |
|---|---|---|
| 1 | 整机主控为 H743；F407 仅前期调试 | 2026-08-25 |
| 2 | 压缩机/蒸发风机/冷凝器风机全部经 Driver（Pico ESC）驱动 | 2026-08-25 |
| 3 | EXV 实际接 UART7 | 2026-08-25 |
| 4 | Driver 用 UART6（921600） | 2026-08-25 |
| 5 | 压力软件量全程绝对压力 (bara) | 2026-08-25 |
| 6 | 传感器 999 哨兵必须原样上传组态屏 | 2026-08-25 |
| 7 | error_reg：触摸屏手动复位 | 2026-08-26 |
| 8 | 所有报警→动作必须经延时（无零延时跳闸） | 2026-08-26 |
| 9 | 启停顺序写死：先风后机；停机先机后风 | 2026-08-26 |
| 10 | 目录不重构：设备逻辑进各设备目录；安全进 Alarm/ | 2026-08-26 |

### 11.2 设计决策（2026-09-02 系统主逻辑补设计）

| # | 决策 | 拍板结论 |
|---|---|---|
| Q1 | 排气温度是否做保护？ | **做 L1 降容 + L2 延时停机** |
| Q2 | L1 限载是否也走延时？ | **是**，L1 走延时槽 |
| Q3 | 重试锁定 L3 是否要做？ | **做**，条件消失即清 `retry_lockout` |
| Q4 | Modbus 通讯断是否 fail-safe 停机？ | **延时 60s 后正常停机**（不锁存） |
| Q5 | 流量报警位 | 为后续带水回路版本预留 `FLOW_FAULT`；当前不接入实体 DI，`FLOW_ENABLE` 保持 0；沿用 `error_reg` bit 15 / 1x16 点位 |
| Q6 | 被控变量是什么？ | **蒸发器出口空气温度 `IDX_OUTLET_T`** |
| Q7 | `setpoint` 的单位与物理量 | **22.0°C**，蒸发器出口空气温度目标 |
| Q8 | 系统类型 | **风冷式直膨制冷；蒸发侧直膨、无水/载冷剂回路** |
| Q9 | 是否需要除霜？ | 当前样机不实现除霜；需验证限定工况不导致蒸发器结霜，低温/高湿扩展工况另行设计 |
| Q10 | 是否要制热模式？ | **不做**，硬件无四通阀 |
| Q11 | `HP_TARGET` 与 `setpoint` 初值 | **12.0 bara** / **22.0°C** |

### 11.3 时序变更（2026-09-03）

| # | 变更 | 说明 |
|---|---|---|
| 1 | 启动顺序修订 | 冷凝风机（S1）→ 蒸发风机（S2）→ EXV（S3）→ 延时后压缩机（S4） |
| 2 | EXV → 压缩机延时 | 引用 `delay_slots[EXV_TO_COMP_DELAY_SLOT]`，用户可配 |
| 3 | 停机顺序修订 | EXV 关闭 → 延时停压缩机 → 延时停风机 |
| 4 | 废弃固定时间参数 | 改为引用延时槽 |

### 11.4 转速环设计（2026-10-11 口径确认）

**架构变更历史**：
- **v3.2（2026-09-07）**：双环控制，外环输出 rpm_cmd，内环转速环输出 throttle_cmd
- **v3.3（2026-09-08）**：外环输出 throttle，转速环按独立开关运行

| # | 决策 | 当前拍板结论 |
|---|---|---|
| 1 | 是否增加转速环？ | **是**，作为可选的转速约束/保护机制（三个独立开关控制） |
| 2 | 转速环实施范围 | **压缩机 + 冷凝风机 + 蒸发风机**（三个执行机构） |
| 3 | 控制架构 | 外环（或手动设定）输出基础油门；设备层按基础油门映射 `rpm_cmd`，转速环启用时使用 RPM 反馈 PID 调节 ESC 油门 |
| 4 | 转速环角色 | 转速约束/保护层；实际实现是围绕油门映射目标转速的闭环调节，并非仅超速时才输出负调节的单向限幅器 |
| 5 | 转速环周期 | **0.5s**；启用后按反馈调节，禁用后基础油门开环输出 |
| 6 | supervisor 联锁输出 | 保持 `*_throttle_cmd`（油门绝对值），不改为 rpm_cmd |
| 7 | 手动模式参数 | 油门值（throttle，48-2047）；手动模式改变基础油门来源，不改变三个转速环使能开关。开关由操作者选择，禁用时开环且无转速反馈约束。2026-09-19 四区重构后地址：手动使能 coils 0x2、手动油门 4x220/4x221、蒸发风机用户油门 4x222、EXV 开度 4x223 |
| 8 | 启动时序参数 | `COMP_START_THROTTLE` / `COND_FAN_START_THROTTLE` / `EVAP_FAN_START_THROTTLE`（油门） |
| 9 | 转速环开关 | `COMP_SPEED_LOOP_ENABLE` / `COND_FAN_SPEED_LOOP_ENABLE` / `EVAP_FAN_SPEED_LOOP_ENABLE`（三个独立开关） |
| 10 | 晋级判据 | 转速稳定性检查：10 秒窗口内转速变化 < 100 rpm（模式 1） |
| 11 | 转速环实现位置 | `Compressor/control.py` / `Condenser/control.py` / `Evaporator/control.py`；公共接口由各 `__init__.py` 导出 |

**当前设计口径说明**：
- 外环直接输出油门更直观，易于调试和手动控制
- 转速环以转速约束/保护为目的；开关独立，手动模式下操作者可按现场需求启用或禁用，不作强制切换
- 启动时以恒定油门启动更简单可靠，无需斜坡
- 晋级判据基于转速稳定性，不依赖目标转速到位
- 转速环启用时，自动与手动模式使用相同的 RPM 反馈调节机制；区别仅在基础油门来源
- `CURRENT_BAD_ACTION=0` 的语义为仅告警，`=1` 为延时停机并锁存

---

## 附录：文档参考

- 进度与问题台账：`PLAN.md`
- 验收报告（含历史审计报告归档）：`REPORT.md`
- 文件结构与索引：`STRUCTURE.md`
- Modbus 点位表：`MODBUS_POINT_TABLE.md`
- 开发规范：`AI_DEVELOPMENT_RULES.md`

---

## 附录 B：Modbus 点表重构计划（已实施，2026-09-19）

> 本节为历史改造计划的完整归档，原文件 `MODBUS_REFACTOR_PLAN.md` 已并入本文档并移至 `archive/`。
> **实施差异说明**：计划 §3.1 的 coil 顺序为 0x7=晋级模式 / 0x8=故障复位，实际实施调换为
> **0x7=故障复位 / 0x8=晋级模式**（与 `docs/参考.csv` 一致）；其余按计划落地。
> 现行权威点表为 `MODBUS_POINT_TABLE.md`（v4.1）。

### Modbus 点表重构计划原文

#### 一、背景

为适配威纶通触摸屏，需要按照参考.csv重新规划Modbus点表，使用标准Modbus功能码。

#### 二、核心变更

##### 2.1 功能码使用规范

| 功能码 | 地址表示 | 数据类型 | 读写属性 | 用途 |
|--------|----------|----------|----------|------|
| 01 | 0x | 位 | 可读写 | 开关类控制（Coil） |
| 02 | 1x | 位 | 只读 | 报警状态（Discrete Input） |
| 03 | 4x | 字（16位） | 可读写 | 参数配置（Holding Register） |
| 04 | 3x | 字（16位） | 只读 | 传感器数据和状态（Input Register） |
| 05 | 0x | 位 | 写单个 | 写单个Coil |
| 06 | 4x | 字 | 写单个 | 写单个Holding Register |
| 15 | 0x | 位 | 写多个 | 写多个Coil（可选） |
| 16 | 4x | 字 | 写多个 | 写多个Holding Register |

##### 2.2 地址映射原则

- **Modbus地址从1开始**（用户视角）
- **代码索引从0开始**（内部实现）
- **转换关系**：Modbus地址 = 代码索引 + 1

#### 三、点表设计

##### 3.1 Coil（0x，功能码01/05/15）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 说明 |
|---------|-----------|---------|---------|------|
| 系统使能开关 | 1 | coils[0] | hr[0] | 0=停机 1=运行 |
| 手动模式使能 | 2 | coils[1] | hr[10] | 0=自动 1=手动（控制压缩机/冷凝风机/EXV） |
| 压缩机转速环使能 | 3 | coils[2] | hr[69] | 0=禁用 1=启用转速闭环 |
| 冷凝风机转速环使能 | 4 | coils[3] | hr[70] | 0=禁用 1=启用转速闭环 |
| 蒸发风机转速环使能 | 5 | coils[4] | hr[71] | 0=禁用 1=启用转速闭环 |
| 流量检查使能 | 6 | coils[5] | hr[93] | 0=禁用 1=启用流量保护 |
| 启动晋级模式 | 7 | coils[6] | hr[65] | 0=定速保持 1=转速稳定性 |
| 故障复位 | 8 | coils[7] | hr[49] | 写1触发复位，自动清零 |

##### 3.2 Discrete Input（1x，功能码02）

**已实现，只需核对地址映射**

| 点位名称 | Modbus地址 | 代码索引 | error_reg位 | 说明 |
|---------|-----------|---------|------------|------|
| EXV故障 | 1 | di[0] | bit 0 | 1=正常 0=故障 |
| 压缩机故障 | 2 | di[1] | bit 1 | 1=正常 0=故障 |
| 蒸发风机故障 | 3 | di[2] | bit 2 | 1=正常 0=故障 |
| 冷凝风机故障 | 4 | di[3] | bit 3 | 1=正常 0=故障 |
| 高压保护 | 5 | di[4] | bit 4 | 1=正常 0=故障 |
| 低压保护 | 6 | di[5] | bit 5 | 1=正常 0=故障 |
| 排气温度保护 | 7 | di[6] | bit 6 | 1=正常 0=故障 |
| 过流保护 | 8 | di[7] | bit 7 | 1=正常 0=故障 |
| LCD通讯故障 | 9 | di[8] | bit 8 | 1=正常 0=故障 |
| Modbus通讯故障 | 10 | di[9] | bit 9 | 1=正常 0=故障 |
| 低过热度保护 | 11 | di[10] | bit 10 | 1=正常 0=故障 |
| 配置IO故障 | 12 | di[11] | bit 11 | 1=正常 0=故障 |
| 吸气压力传感器故障 | 13 | di[12] | bit 12 | 1=正常 0=故障 |
| 排气压力传感器故障 | 14 | di[13] | bit 13 | 1=正常 0=故障 |
| 电流传感器故障 | 15 | di[14] | bit 14 | 1=正常 0=故障 |
| 传感器超龄故障 | 16 | di[15] | bit 15 | 1=正常 0=故障 |
| 流量保护 | 17 | di[16] | bit 16 | 1=正常 0=故障 |
| 保留 | 18-32 | di[17-31] | bit 17-31 | 预留 |

##### 3.3 Holding Register（4x，功能码03/06/16）

**按参考.csv重新映射地址**

###### 过热度控制参数（地址1-8）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 过热度PID_Kp | 1 | hr[0] | hr[1] | x100 | Kp=寄存器值/100 |
| 过热度PID_Ki | 2 | hr[1] | hr[2] | x100 | Ki=寄存器值/100 |
| 过热度PID_Kd | 3 | hr[2] | hr[3] | x100 | Kd=寄存器值/100 |
| 过热度设定值 | 4 | hr[3] | hr[4] | x10 | 单位K |
| 过热度误差阈值 | 5 | hr[4] | hr[5] | x10 | 单位K |
| 过热度死区 | 6 | hr[5] | hr[6] | x10 | 单位K |
| 过热度最大变化量 | 7 | hr[6] | hr[7] | x1 | EXV单次最大变化 |
| 保留 | 8 | hr[7] | hr[8] | - | - |

###### 容量环参数（地址16-24）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 容量环PID_Kp | 16 | hr[15] | hr[16] | x100 | - |
| 容量环PID_Ki | 17 | hr[16] | hr[17] | x100 | - |
| 容量环PID_Kd | 18 | hr[17] | hr[18] | x100 | - |
| 容量环设定温度 | 19 | hr[18] | hr[19] | x100 | 单位℃ |
| 容量环死区 | 20 | hr[19] | hr[20] | x10 | 单位K |
| 容量环最小油门 | 21 | hr[20] | hr[21] | x1 | 48-2047 |
| 容量环最大油门 | 22 | hr[21] | hr[22] | x1 | 48-2047 |
| 容量环最大变化量 | 23 | hr[22] | hr[23] | x1 | - |
| 容量环周期 | 24 | hr[23] | hr[24] | x1 | 单位秒 |

###### 冷凝压力环参数（地址25-30）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 冷凝压力环PID_Kp | 25 | hr[24] | hr[25] | x100 | - |
| 冷凝压力环PID_Ki | 26 | hr[25] | hr[26] | x100 | - |
| 冷凝压力环PID_Kd | 27 | hr[26] | hr[27] | x100 | - |
| 冷凝压力环最小油门 | 28 | hr[27] | hr[28] | x1 | 48-2047 |
| 冷凝压力环最大油门 | 29 | hr[28] | hr[29] | x1 | 48-2047 |
| 冷凝压力环最大变化量 | 30 | hr[29] | hr[30] | x1 | - |

###### 保护阈值（地址31-63）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 高压停机阈值 | 31 | hr[30] | hr[31] | x100 | 单位bar |
| 高压降容阈值 | 32 | hr[31] | hr[32] | x100 | 单位bar |
| 高压告警阈值 | 33 | hr[32] | hr[33] | x100 | 单位bar |
| 冷凝压力目标 | 34 | hr[33] | hr[34] | x100 | 单位bar |
| ... | 35-48 | hr[34-47] | hr[35-48] | - | 保留地址35-49 |
| 低压停机阈值 | 50 | hr[49] | hr[35] | x100 | 单位bar |
| 低压降容阈值 | 51 | hr[50] | hr[36] | x100 | 单位bar |
| 低压告警阈值 | 52 | hr[51] | hr[37] | x100 | 单位bar |
| 低压回升迟滞 | 53 | hr[52] | hr[38] | x100 | 单位bar |
| 电流停机阈值 | 54 | hr[53] | hr[39] | x10 | 单位A |
| 电流降容阈值 | 55 | hr[54] | hr[40] | x10 | 单位A |
| 电流禁升阈值 | 56 | hr[55] | hr[41] | x10 | 单位A |
| 电流告警阈值 | 57 | hr[56] | hr[42] | x10 | 单位A |
| 排气温度停机阈值 | 58 | hr[57] | hr[43] | x100 | 单位℃ |
| 排气温度降容阈值 | 59 | hr[58] | hr[44] | x100 | 单位℃ |
| 排气温度告警阈值 | 60 | hr[59] | hr[45] | x100 | 单位℃ |
| 最小过热度 | 61 | hr[60] | hr[46] | x10 | 单位K |
| 临界过热度 | 62 | hr[61] | hr[47] | x10 | 单位K |
| 保留 | 63 | hr[62] | hr[48] | - | - |

###### 延时槽（地址100-107）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 延时槽0 | 100 | hr[99] | hr[50] | x1 | 单位秒，0-3600 |
| 延时槽1 | 101 | hr[100] | hr[51] | x1 | 单位秒 |
| 延时槽2 | 102 | hr[101] | hr[52] | x1 | 单位秒 |
| 延时槽3 | 103 | hr[102] | hr[53] | x1 | 单位秒 |
| 延时槽4 | 104 | hr[103] | hr[54] | x1 | 单位秒 |
| 延时槽5 | 105 | hr[104] | hr[55] | x1 | 单位秒 |
| 延时槽6 | 106 | hr[105] | hr[56] | x1 | 单位秒 |
| 延时槽7 | 107 | hr[106] | hr[57] | x1 | 单位秒 |

###### 启动参数（地址120-123）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| EXV启动开度 | 120 | hr[119] | hr[58] | x10 | 单位% |
| 压缩机启动油门 | 121 | hr[120] | hr[59] | x1 | 48-2047 |
| 冷凝风机启动油门 | 122 | hr[121] | hr[60] | x1 | 48-2047 |
| 蒸发风机启动油门 | 123 | hr[122] | hr[61] | x1 | 48-2047 |

###### 延时槽号（地址130-132）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| EXV到压缩机延时槽号 | 130 | hr[129] | hr[62] | x1 | 0-7 |
| 停机压缩机延时槽号 | 131 | hr[130] | hr[63] | x1 | 0-7 |
| 停机风机延时槽号 | 132 | hr[131] | hr[64] | x1 | 0-7 |

###### 压缩机参数（地址150-157）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 压缩机启动延时 | 150 | hr[149] | hr[66] | x1 | 单位秒 |
| 压缩机转速稳定窗口 | 151 | hr[150] | hr[67] | x1 | 单位秒 |
| 压缩机转速稳定阈值 | 152 | hr[151] | hr[68] | x1 | 单位rpm |
| 压缩机最大转速 | 153 | hr[152] | hr[72] | x1 | 单位rpm |
| 压缩机转速环Kp | 154 | hr[153] | hr[73] | x100 | - |
| 压缩机转速环Ki | 155 | hr[154] | hr[74] | x100 | - |
| 压缩机转速环死区 | 156 | hr[155] | hr[75] | x1 | 单位rpm |
| 压缩机最大调节量 | 157 | hr[156] | hr[76] | x1 | - |

###### 风机参数（地址180-189）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 风机最大转速 | 180 | hr[179] | hr[77] | x1 | 单位rpm |
| 风机转速环Kp | 181 | hr[180] | hr[78] | x100 | - |
| 风机转速环Ki | 182 | hr[181] | hr[79] | x100 | - |
| 风机转速环死区 | 183 | hr[182] | hr[80] | x1 | 单位rpm |
| 风机最大调节量 | 184 | hr[183] | hr[81] | x1 | - |
| 压缩机回油最低油门 | 185 | hr[184] | hr[82] | x1 | 48-500 |
| 降容速率 | 186 | hr[185] | hr[83] | x1 | throttle/s |
| 保留_3 | 187 | hr[186] | - | - | - |
| 保留_4 | 188 | hr[187] | - | - | - |
| 保留_5 | 189 | hr[188] | - | - | - |

###### 时间保护参数（地址200-205）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 最短运行时间 | 200 | hr[199] | hr[87] | x1 | 单位秒 |
| 最短停机间隔 | 201 | hr[200] | hr[88] | x1 | 单位秒 |
| 每小时最大启动次数 | 202 | hr[201] | hr[89] | x1 | 1-10 |
| 最大重试次数 | 203 | hr[202] | hr[90] | x1 | 1-10 |
| 重试窗口 | 204 | hr[203] | hr[91] | x1 | 单位秒 |
| Modbus无帧超时 | 205 | hr[204] | hr[92] | x1 | 单位秒 |

###### 手动油门（地址220-223）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 压缩机手动油门 | 220 | hr[219] | hr[12] | x1 | 0-2047 |
| 冷凝风机手动油门 | 221 | hr[220] | hr[13] | x1 | 0-2047 |
| 蒸发风机用户油门 | 222 | hr[221] | hr[14] | x1 | 0-2047 |
| EXV手动开度 | 223 | hr[222] | hr[15] | x10 | 单位% |

##### 3.4 Input Register（3x，功能码04）

**只读监测数据，按参考.csv重新映射地址**

###### 传感器数据（地址1-15）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 吸气温度 | 1 | ir[0] | ir[0] | x100 | 单位℃ |
| 排气温度 | 2 | ir[1] | ir[1] | x100 | 单位℃ |
| 蒸发器入口空气温度 | 3 | ir[2] | ir[2] | x100 | 单位℃ |
| 蒸发器出口空气温度 | 4 | ir[3] | ir[3] | x100 | 容量环被控温度 |
| 液管温度 | 5 | ir[4] | ir[4] | x100 | 单位℃ |
| 排气压力 | 6 | ir[5] | ir[5] | x100 | 单位bar |
| 吸气压力 | 7 | ir[6] | ir[6] | x100 | 单位bar |
| 过热度 | 8 | ir[7] | ir[7] | x10 | 单位K |
| 过冷度 | 9 | ir[8] | ir[8] | x10 | 单位K |
| EXV当前开度 | 10 | ir[9] | ir[17] | x10 | 单位% |
| EXV目标开度 | 11 | ir[10] | ir[18] | x10 | 单位% |
| 整机电流 | 12 | ir[11] | ir[9] | x10 | 单位A |
| 传感器质量码低8位 | 13 | ir[12] | ir[10] | x1 | bit0-7 |
| 传感器质量码高2位 | 14 | ir[13] | ir[11] | x1 | bit0-1 |
| 传感器故障寄存器 | 15 | ir[14] | ir[12] | x1 | 1=正常 0=故障 |

###### 系统状态（地址100-102）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 系统模式 | 100 | ir[99] | ir[13] | x1 | 0=BOOT到7=DEGRADED |
| 启动阶段 | 101 | ir[100] | ir[30] | x1 | 0-6对应S0-S6 |
| 运行子状态 | 102 | ir[101] | ir[14] | x1 | 0=R1到3=R4 |

###### 故障寄存器（地址110-111）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 故障寄存器低16位 | 110 | ir[109] | ir[15] | x1 | error_reg bit0-15 |
| 故障寄存器高16位 | 111 | ir[110] | ir[16] | x1 | error_reg bit16-31 |

###### 执行机构状态（地址200-223）

| 点位名称 | Modbus地址 | 代码索引 | 当前位置 | 倍率 | 说明 |
|---------|-----------|---------|---------|------|------|
| 压缩机当前油门 | 200 | ir[199] | ir[19] | x1 | 0-2047 |
| 压缩机当前转速 | 201 | ir[200] | ir[20] | x1 | 单位rpm |
| 蒸发风机当前油门 | 202 | ir[201] | ir[21] | x1 | 0-2047 |
| 蒸发风机当前转速 | 203 | ir[202] | ir[22] | x1 | 单位rpm |
| 冷凝风机当前油门 | 204 | ir[203] | ir[23] | x1 | 0-2047 |
| 冷凝风机当前转速 | 205 | ir[204] | ir[24] | x1 | 单位rpm |
| 保留_6 | 206 | ir[205] | - | - | - |
| ESC0压缩机转速 | 207 | ir[206] | ir[33] | x1 | 单位rpm |
| ESC0压缩机温度 | 208 | ir[207] | ir[34] | x1 | 单位℃ |
| ESC0压缩机电压 | 209 | ir[208] | ir[35] | x10 | 单位V |
| ESC0压缩机电流 | 210 | ir[209] | ir[36] | x10 | 单位A |
| ESC1蒸发风机转速 | 211 | ir[210] | ir[37] | x1 | 单位rpm |
| ESC1蒸发风机温度 | 212 | ir[211] | ir[38] | x1 | 单位℃ |
| ESC1蒸发风机电压 | 213 | ir[212] | ir[39] | x10 | 单位V |
| ESC1蒸发风机电流 | 214 | ir[213] | ir[40] | x10 | 单位A |
| ESC2冷凝风机转速 | 215 | ir[214] | ir[41] | x1 | 单位rpm |
| ESC2冷凝风机温度 | 216 | ir[215] | ir[42] | x1 | 单位℃ |
| ESC2冷凝风机电压 | 217 | ir[216] | ir[43] | x10 | 单位V |
| ESC2冷凝风机电流 | 218 | ir[217] | ir[44] | x10 | 单位A |
| ESC3备用转速 | 219 | ir[218] | ir[45] | x1 | 单位rpm |
| ESC3备用温度 | 220 | ir[219] | ir[46] | x1 | 单位℃ |
| ESC3备用电压 | 221 | ir[220] | ir[47] | x10 | 单位V |
| ESC3备用电流 | 222 | ir[221] | ir[48] | x10 | 单位A |
| L1限载动作码 | 223 | ir[222] | ir[49] | x1 | 位图 |

#### 四、代码改造任务清单

##### 4.1 Display_Touch/modbus_regs.py

###### 任务1：新增Coil数组
```python
self.coils = [0] * 8  # 0x地址1-8对应coils[0-7]
```

###### 任务2：扩展Holding Register和Input Register数组
```python
self.holding_regs = [0] * 250   # 扩展到250，支持地址1-223
self.input_regs = [0] * 250     # 扩展到250，支持地址1-223
```

##### 4.2 Display_Touch/modbus_slave.py

###### 任务3：实现功能码01（读Coil）
```python
def _handle_read_coils(addr, fc, req):
    start = (req[2] << 8) | req[3]
    qty   = (req[4] << 8) | req[5]
    if qty < 1 or qty > 2000:
        _exception(addr, fc, 0x03); return
    # 地址转索引：start-1
    bits = regs.read_block(regs.coils, start-1, qty)
    if bits is None:
        _exception(addr, fc, 0x02); return
    # 封装响应（与功能码02类似）
    ...
```

###### 任务4：实现功能码05（写单个Coil）
```python
def _handle_write_single_coil(addr, fc, req):
    coil_addr = (req[2] << 8) | req[3]
    value = (req[4] << 8) | req[5]
    # 标准Modbus：0xFF00=ON，0x0000=OFF
    if value != 0xFF00 and value != 0x0000:
        _exception(addr, fc, 0x03); return
    # 地址转索引
    idx = coil_addr - 1
    if idx < 0 or idx >= len(regs.coils):
        _exception(addr, fc, 0x02); return
    # 写入
    regs.coils[idx] = 1 if value == 0xFF00 else 0
    # 同步到state（见任务5）
    _sync_coils_to_state([idx])
    # 响应：回显请求
    _send(req[:6])
```

###### 任务5：Coil与state的双向同步
```python
def _sync_coils_to_state(changed_indices):
    """Coil变化后同步到state"""
    for idx in changed_indices:
        if idx == 0:  # 系统使能
            state.params['system_enable'] = regs.coils[0]
        elif idx == 1:  # 手动模式
            state.manual_params['MANUAL_MODE_ENABLE'] = regs.coils[1]
        elif idx == 2:  # 压缩机转速环使能
            state.params['comp_speed_loop_en'] = regs.coils[2]
        # ... 其他映射
        elif idx == 7:  # 故障复位（脉冲）
            if regs.coils[7]:
                state.fault_reset_request = True
                regs.coils[7] = 0  # 自动清零

def _sync_state_to_coils():
    """state变化后同步到Coil（定期调用）"""
    regs.coils[0] = state.params.get('system_enable', 0)
    regs.coils[1] = state.manual_params.get('MANUAL_MODE_ENABLE', 0)
    regs.coils[2] = state.params.get('comp_speed_loop_en', 1)
    # ... 其他映射
```

###### 任务6：重新映射Holding Register地址
将所有hr写入处理改为稀疏地址映射：
```python
# 当前：if 10 in t: state.manual_params['MANUAL_MODE_ENABLE'] = hr[10]
# 改为：删除（已移到Coil）

# 当前：if 1 in t: state.params['sh_pid_kp'] = hr[1]
# 改为：if 1 in t: state.params['sh_pid_kp'] = hr[0]  # 地址1对应索引0

# 当前：if 50 in t: state.params['delay_slot_0'] = hr[50]
# 改为：if 100 in t: state.params['delay_slot_0'] = hr[99]  # 地址100对应索引99
```

###### 任务7：重新映射Input Register地址
```python
# 当前：ir[0] = sensor_data[0]  # 地址0
# 改为：ir[0] = sensor_data[0]  # 地址1，索引0

# 当前：ir[13] = state.sys_mode
# 改为：ir[99] = state.sys_mode  # 地址100，索引99

# 当前：ir[30] = state.startup_stage
# 改为：ir[100] = state.startup_stage  # 地址101，索引100
```

###### 任务8：修改功能码03/04/06/16的地址转换
```python
# 当前实现假设地址从0开始
# 需要改为：Modbus地址 = 索引 + 1

def _handle_read_regs(addr, fc, req, table):
    start = (req[2] << 8) | req[3]
    qty   = (req[4] << 8) | req[5]
    # 新增：地址转索引（地址1对应索引0）
    start_idx = start - 1
    vals = regs.read_block(table, start_idx, qty)
    ...
```

##### 4.3 Board/params_store.py

###### 任务9：更新持久化参数列表
```python
# 原：持久化hr[1-8], hr[16-48], hr[50-84], hr[87-94]
# 改为：持久化hr[0-7], hr[15-62], hr[99-188], hr[199-204]
# 注意：索引=地址-1
```

##### 4.4 测试文件

###### 任务10：更新所有测试用例的地址引用
- test_step5a_verification.py
- test_step6_verification.py
- test_step7_verification.py

需要修改所有hr/ir地址的断言。

#### 五、实施步骤

##### 第一阶段：基础设施（2-3小时）
1. 修改modbus_regs.py，新增coils数组，扩展hr/ir数组
2. 实现功能码01/05处理函数
3. 实现Coil与state的双向同步函数

##### 第二阶段：地址重映射（3-4小时）
4. 创建地址映射表（dict或常量）
5. 重构hr写入处理逻辑（_handle_write_single/_handle_write_multiple）
6. 重构ir更新逻辑（modbus_slave_task主循环）
7. 修改功能码处理函数的地址转换逻辑

##### 第三阶段：测试验证（2-3小时）
8. 更新单元测试用例
9. 编写Coil功能测试用例
10. 回归测试所有功能码

##### 第四阶段：文档更新（1小时）
11. 更新MODBUS_POINT_TABLE.csv
12. 更新DESIGN.md中的Modbus章节
13. 生成最终点表文档供触摸屏配置使用

#### 六、风险点与注意事项

##### 6.1 地址映射错误
- **风险**：地址映射表维护复杂，容易出错
- **对策**：使用字典集中管理映射关系，代码生成减少手工编写

##### 6.2 Coil脉冲信号
- **风险**：故障复位的脉冲逻辑可能不触发
- **对策**：在写入Coil后立即调用同步函数，确保state.fault_reset_request被设置

##### 6.3 持久化参数
- **风险**：地址变更后，旧Flash数据恢复到错误位置
- **对策**：增加版本校验，版本不匹配时使用默认值

##### 6.4 兼容性
- **风险**：改造期间现有测试全部失败
- **对策**：分支开发，保留旧代码可回滚

#### 七、完成标准

- [ ] 功能码01/05/15可正常读写Coil
- [ ] 8个Coil点位与state双向同步正常
- [ ] 功能码02读取32个报警位正常
- [ ] 功能码03/04按新地址映射读取正常
- [ ] 功能码06/16按新地址映射写入正常
- [ ] 所有参数持久化正常
- [ ] 单元测试全部通过
- [ ] 生成最终点表CSV文档

#### 八、工作量估算

| 阶段 | 工作量 | 负责人 | 预计完成时间 |
|------|--------|--------|------------|
| 基础设施 | 2-3小时 | - | - |
| 地址重映射 | 3-4小时 | - | - |
| 测试验证 | 2-3小时 | - | - |
| 文档更新 | 1小时 | - | - |
| **总计** | **8-11小时** | - | - |

---

**文档版本**：v1.0  
**创建日期**：2026-01-XX  
**最后更新**：2026-01-XX

## 附录 C：液管焓值添加改造计划（已实施，2026-09-22 v4.1 落地）

> 本节为历史改造计划的完整归档，原文件 `ENTHALPY_ADDITION_PLAN.md` 已并入本文档并移至 `archive/`。

### 液管焓值添加改造计划原文

**创建日期**：2026-01-XX  
**优先级**：P1（中等）  
**预计工作量**：2-3小时  
**改造人员**：待分配

---

#### 一、问题描述

##### 1.1 当前状况
- ✅ 已实现：吸气焓值计算（`sensor_data[8]`）
  - 计算方式：`read_E(吸气压力, 吸气温度)`
  - 映射到3x位置：需确认
  
- ❌ 缺失：液管焓值计算
  - 应计算：`read_E(排气压力, 液管温度)`
  - 用途：冷凝器出口焓值，用于计算制冷量、COP等性能参数

##### 1.2 标准.csv要求
```
3x10: 进口焓（吸气焓）
3x11: 出口焓（液管焓）← 当前缺失
```

##### 1.3 影响
- 无法通过Modbus读取液管焓值
- 无法计算制冷量：Q = m * (h_吸气 - h_液管)
- 无法计算COP：COP = Q / P_输入
- 点位表与标准.csv不一致

---

#### 二、改造方案

##### 2.1 点位插入策略

**在3x9（过冷度）后插入2个焓值点位，其余点位顺延**

###### 变更前：
```
3x1-3x5:   温度（ir[0-4]）
3x6-3x7:   压力（ir[5-6]）
3x8:       过热度（ir[7]）
3x9:       过冷度（ir[8]）
3x10:      EXV当前开度（ir[9]）
3x11:      EXV目标开度（ir[10]）
3x12:      整机电流（ir[11]）
3x13-3x14: 质量码（ir[12-13]）
3x15:      传感器故障位图（ir[14]）
3x100+:    系统状态（ir[99+]）不变
```

###### 变更后：
```
3x1-3x5:   温度（ir[0-4]）          不变
3x6-3x7:   压力（ir[5-6]）          不变
3x8:       过热度（ir[7]）          不变
3x9:       过冷度（ir[8]）          不变
3x10:      进口焓（ir[9]）          ← 新增
3x11:      出口焓（ir[10]）         ← 新增
3x12:      EXV当前开度（ir[11]）    ← 从ir[9]移动
3x13:      EXV目标开度（ir[12]）    ← 从ir[10]移动
3x14:      整机电流（ir[13]）       ← 从ir[11]移动
3x15-3x16: 质量码（ir[14-15]）      ← 从ir[12-13]移动
3x17:      传感器故障位图（ir[16]） ← 从ir[14]移动
3x100+:    系统状态（ir[99+]）      不变
```

---

#### 三、具体实施步骤

##### Step 1: 添加state变量（Board/state.py）

**文件位置**：约第36行（subcooling定义后）

**修改内容**：
```python
# 过冷度（派生通道，2026-09-19 Modbus 重构新增，映射 3x9）
# 计算：饱和温度(排气压力) - 液管温度；无法计算时 999
subcooling = 999

# 液管焓值（派生通道，2026-XX-XX 新增，映射 3x11）
# 计算：read_E(排气压力, 液管温度)；无法计算时 999
liquid_enthalpy = 999  # ← 新增这一行
```

**改造要点**：
- 变量名：`liquid_enthalpy`
- 初始值：`999`（哨兵值，表示未计算）
- 数据类型：int（焓值 * 100，单位：kJ/kg * 100）

---

##### Step 2: 添加液管焓值计算（Sensor/read_sensors.py）

###### 2.1 同步版本修改

**文件位置**：约第72行（吸气焓值计算后）

**当前代码**：
```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 过冷度 SC：查表失败只影响本通道
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            # ... 后续代码
```

**新增代码**（在过冷度计算前插入）：
```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值：查表失败只影响本通道 ← 新增
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：查表失败只影响本通道
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            # ... 后续代码
```

**改造要点**：
- 输入参数：
  - `sensor_data[6]`：排气压力（IDX_DISCHARGE_PSI）
  - `sensor_data[4]`：液管温度（IDX_LIQUID_T）
- 输出：`state.liquid_enthalpy`
- 异常处理：查表失败时置999

---

###### 2.2 异步版本修改

**文件位置**：约第145行（reads_async函数中）

**修改内容**：与同步版本完全相同，在吸气焓值计算后插入液管焓值计算

```python
        # 焓值 E：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异步异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值：查表失败只影响本通道 ← 新增
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异步异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：查表失败只影响本通道
        # ... 后续代码
```

**改造要点**：
- 与同步版本逻辑完全一致
- 打印信息加上"异步"字样以便区分

---

##### Step 3: 调整Input Register映射（Display_Touch/modbus_slave.py）

**文件位置**：`sync_input_regs()` 函数（约582-643行）

**当前代码**（关键部分）：
```python
def sync_input_regs():
    sd = state.sensor_data
    st = state.state_data
    ir = regs.input_regs

    # 3x1-3x5: 温度 sensor_data[0..4] *100
    for i in range(5):
        ir[i] = _fixed100(sd[i])

    # 3x6 排气压力（高压）/ 3x7 吸气压力（低压）*100
    ir[5] = _fixed100(sd[state.IDX_DISCHARGE_PSI])
    ir[6] = _fixed100(sd[state.IDX_SUCTION_PSI])

    # 3x8 过热度 / 3x9 过冷度 *10
    ir[7] = _fixed10(sd[state.IDX_SUPERHEAT])
    ir[8] = _fixed10(state.subcooling)

    # 3x10-3x11 EXV 当前/目标开度 *10
    ir[9] = _fixed10(st[state.ST_VALVE_POS])
    ir[10] = _fixed10(state.exv_target_pct)

    # 3x12 整机电流 *10
    ir[11] = _fixed10(sd[state.IDX_CURRENT])

    # 3x13-3x14 质量码（每通道 2 bit；0=OK 1=SUSPECT 2=BAD）
    q_low = 0
    for i in range(8):
        q_low |= (state.sensor_quality[i] & 0x03) << (2 * i)
    ir[12] = q_low
    q_high = 0
    for i in range(8, 10):
        q_high |= (state.sensor_quality[i] & 0x03) << (2 * (i - 8))
    ir[13] = q_high

    # 3x15 传感器分通道故障位图（完整 16-bit）
    ir[14] = state.sensor_fault_reg
    
    # ... 后续代码（3x100+）不变
```

**修改后代码**：
```python
def sync_input_regs():
    sd = state.sensor_data
    st = state.state_data
    ir = regs.input_regs

    # 3x1-3x5: 温度 sensor_data[0..4] *100
    for i in range(5):
        ir[i] = _fixed100(sd[i])

    # 3x6 排气压力（高压）/ 3x7 吸气压力（低压）*100
    ir[5] = _fixed100(sd[state.IDX_DISCHARGE_PSI])
    ir[6] = _fixed100(sd[state.IDX_SUCTION_PSI])

    # 3x8 过热度 / 3x9 过冷度 *10
    ir[7] = _fixed10(sd[state.IDX_SUPERHEAT])
    ir[8] = _fixed10(state.subcooling)

    # 3x10 进口焓（吸气焓）/ 3x11 出口焓（液管焓）*100 ← 新增
    ir[9] = _fixed100(sd[state.IDX_E_VALUE])
    ir[10] = _fixed100(state.liquid_enthalpy)

    # 3x12-3x13 EXV 当前/目标开度 *10 ← 索引从9-10改为11-12
    ir[11] = _fixed10(st[state.ST_VALVE_POS])
    ir[12] = _fixed10(state.exv_target_pct)

    # 3x14 整机电流 *10 ← 索引从11改为13
    ir[13] = _fixed10(sd[state.IDX_CURRENT])

    # 3x15-3x16 质量码 ← 索引从12-13改为14-15
    q_low = 0
    for i in range(8):
        q_low |= (state.sensor_quality[i] & 0x03) << (2 * i)
    ir[14] = q_low
    q_high = 0
    for i in range(8, 10):
        q_high |= (state.sensor_quality[i] & 0x03) << (2 * (i - 8))
    ir[15] = q_high

    # 3x17 传感器分通道故障位图 ← 索引从14改为16
    ir[16] = state.sensor_fault_reg
    
    # ... 后续代码（3x100+）不变
```

**改造要点**：
- ir[9-10]：新增焓值映射（*100倍率）
- ir[11-16]：原ir[9-14]整体后移2位
- ir[99+]：高位地址不受影响

---

##### Step 4: 更新参考.csv（docs/参考.csv）

**文件位置**：当前第8-15行

**当前内容**（推测）：
```csv
过热度,MODBUS RTU,3x,8
过冷度,MODBUS RTU,3x,9
EXV当前开度,MODBUS RTU,3x,10
EXV目标开度,MODBUS RTU,3x,11
整机电流,MODBUS RTU,3x,12
质量码低,MODBUS RTU,3x,13
质量码高,MODBUS RTU,3x,14
传感器故障位图,MODBUS RTU,3x,15
```

**修改后内容**：
```csv
过热度,MODBUS RTU,3x,8
过冷度,MODBUS RTU,3x,9
进口焓,MODBUS RTU,3x,10               ← 新增（吸气焓，*100，单位：kJ/kg*100）
出口焓,MODBUS RTU,3x,11               ← 新增（液管焓，*100，单位：kJ/kg*100）
EXV当前开度,MODBUS RTU,3x,12          ← 从3x10改为3x12
EXV目标开度,MODBUS RTU,3x,13          ← 从3x11改为3x13
整机电流,MODBUS RTU,3x,14             ← 从3x12改为3x14
质量码低,MODBUS RTU,3x,15             ← 从3x13改为3x15
质量码高,MODBUS RTU,3x,16             ← 从3x14改为3x16
传感器故障位图,MODBUS RTU,3x,17      ← 从3x15改为3x17
```

**改造要点**：
- 在3x9后插入2行
- 后续点位地址全部+2
- 3x100+点位不受影响（稀疏地址）

---

#### 四、测试计划

##### 4.1 单元测试（Python脚本）

###### 测试用例1：液管焓值计算正确性
```python
import Board.state as state
from Extensions.read_r134a import read_E

# 模拟传感器数据
state.sensor_data[6] = 18.5  # 排气压力 18.5 bar
state.sensor_data[4] = 35.0  # 液管温度 35°C

# 手动计算期望值
expected = read_E(18.5, 35.0)

# 执行传感器采集
from Sensor.read_sensors import reads_async
await reads_async()

# 验证结果
assert state.liquid_enthalpy == int(expected * 100) / 100
print(f"液管焓值测试通过: {state.liquid_enthalpy} kJ/kg")
```

###### 测试用例2：异常处理
```python
# 模拟压力超范围
state.sensor_data[6] = 0.01  # 极低压力，触发查表异常

await reads_async()

# 验证异常处理
assert state.liquid_enthalpy == 999
print("异常处理测试通过")
```

---

##### 4.2 Modbus集成测试

###### 测试用例3：点位映射正确性
使用Modbus Poll或pymodbus读取：

```python
from pymodbus.client import ModbusSerialClient

client = ModbusSerialClient(
    method='rtu',
    port='/dev/ttyUSB0',
    baudrate=115200,
    timeout=1
)

# 读取3x10-3x17（焓值 + 原有点位）
result = client.read_input_registers(address=9, count=8, slave=1)

print(f"3x10 进口焓: {result.registers[0] / 100} kJ/kg")
print(f"3x11 出口焓: {result.registers[1] / 100} kJ/kg")
print(f"3x12 EXV当前开度: {result.registers[2] / 10} %")
print(f"3x13 EXV目标开度: {result.registers[3] / 10} %")
print(f"3x14 整机电流: {result.registers[4] / 10} A")
print(f"3x15 质量码低: {bin(result.registers[5])}")
print(f"3x16 质量码高: {bin(result.registers[6])}")
print(f"3x17 传感器故障: {bin(result.registers[7])}")
```

**预期结果**：
- 3x10: 吸气焓值（例如420.50 kJ/kg → 读取42050）
- 3x11: 液管焓值（例如258.30 kJ/kg → 读取25830）
- 3x12-3x17: 与原3x10-3x15值一致

---

###### 测试用例4：高位地址不受影响
```python
# 读取3x100-3x102（系统状态）
result = client.read_input_registers(address=99, count=3, slave=1)

print(f"3x100 系统模式: {result.registers[0]}")
print(f"3x101 启动阶段: {result.registers[1]}")
print(f"3x102 运行子状态: {result.registers[2]}")
```

**预期结果**：与改造前完全一致

---

##### 4.3 性能测试

###### 测试用例5：查表性能
```python
import time

# 测试1000次查表耗时
start = time.ticks_ms()
for _ in range(1000):
    read_E(18.5, 35.0)
elapsed = time.ticks_diff(time.ticks_ms(), start)

print(f"1000次查表耗时: {elapsed} ms")
print(f"单次平均: {elapsed / 1000} ms")
```

**性能要求**：单次查表 < 5ms（与吸气焓值查表一致）

---

##### 4.4 边界测试

###### 测试用例6：边界条件
```python
# 测试极端压力/温度
test_cases = [
    (0.05, -40),   # 最低压力，极低温
    (30.0, 80),    # 最高压力，极高温
    (10.0, 0),     # 饱和状态（dT=0）
    (10.0, -5),    # 过冷状态（dT<0）
    (10.0, 10),    # 过热状态（dT>0）
]

for psi, temp in test_cases:
    try:
        h = read_E(psi, temp)
        print(f"P={psi} bar, T={temp}°C → H={h} kJ/kg")
    except Exception as e:
        print(f"P={psi} bar, T={temp}°C → 异常: {e}")
```

---

#### 五、风险评估

##### 5.1 高风险项

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 点位后移导致触摸屏配置失效 | 高 | 高 | 改造前备份触摸屏配置，改造后逐点核对 |
| 液管焓值计算错误 | 中 | 中 | 与手工计算对比验证 |
| 查表性能影响采集周期 | 低 | 中 | 性能测试，必要时优化查表算法 |

##### 5.2 中风险项

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| 异常处理不完善 | 中 | 低 | 增加边界测试用例 |
| 文档更新遗漏 | 中 | 低 | 改造后全面核对文档 |

---

#### 六、回滚方案

##### 6.1 回滚步骤

1. **恢复state.py**：删除`liquid_enthalpy`变量
2. **恢复read_sensors.py**：删除液管焓值计算代码（2处）
3. **恢复modbus_slave.py**：将ir[11-16]映射改回ir[9-14]
4. **恢复参考.csv**：删除进口焓/出口焓两行，后续点位地址-2

##### 6.2 回滚触发条件

- 液管焓值计算错误率 > 5%
- 查表性能 > 10ms/次
- Modbus通讯异常率 > 1%
- 触摸屏点位读取错误

---

#### 七、时间计划

| 阶段 | 任务 | 预计耗时 | 负责人 |
|------|------|---------|--------|
| Day 1 | Step1-2：添加液管焓值计算 | 1小时 | TBD |
| Day 1 | Step3：调整Modbus映射 | 0.5小时 | TBD |
| Day 1 | Step4：更新文档 | 0.5小时 | TBD |
| Day 2 | 单元测试 | 1小时 | TBD |
| Day 2 | Modbus集成测试 | 1小时 | TBD |
| Day 2 | 性能+边界测试 | 1小时 | TBD |
| **总计** | | **5小时** | |

---

#### 八、验收标准

##### 8.1 功能验收

- [ ] 液管焓值计算正确（与手工计算误差 < 1%）
- [ ] 3x10读取进口焓（吸气焓）正确
- [ ] 3x11读取出口焓（液管焓）正确
- [ ] 3x12-3x17点位值与原3x10-3x15一致
- [ ] 3x100+点位不受影响
- [ ] 异常处理正确（压力超范围时焓值=999）

##### 8.2 性能验收

- [ ] 单次液管焓值查表 < 5ms
- [ ] 传感器采集周期 < 100ms（不受影响）
- [ ] Modbus响应时间 < 50ms（不受影响）

##### 8.3 稳定性验收

- [ ] 连续运行24小时无异常
- [ ] 液管焓值计算异常率 < 0.1%
- [ ] Modbus通讯成功率 > 99.9%

##### 8.4 文档验收

- [ ] 参考.csv更新完成
- [ ] 改造计划文档完整
- [ ] 测试报告归档

---

#### 九、注意事项

##### 9.1 代码规范
- 变量命名：使用`liquid_enthalpy`（与`subcooling`命名风格一致）
- 异常处理：查表失败时置999，打印日志
- 注释：说明计算公式和单位

##### 9.2 数据单位
- state存储：`int(焓值 * 100) / 100`（保留2位小数）
- Modbus传输：`int(焓值 * 100)`（*100倍率）
- 触摸屏显示：`读取值 / 100`（恢复kJ/kg）

##### 9.3 查表依赖
- 依赖文件：`Extensions/pt_table.bin`
- 依赖函数：`read_E(psi, temp)`
- 异常类型：`ValueError(-999)`或`ValueError(9999)`

##### 9.4 触摸屏配置
改造后需更新威纶通触摸屏配置：
- 3x10 → 进口焓（新增）
- 3x11 → 出口焓（新增）
- 3x12-3x17 → 地址全部+2

---

#### 十、相关文档

- [Modbus重构计划](MODBUS_REFACTOR_PLAN.md)
- [Modbus审核报告](MODBUS_REFACTOR_AUDIT_REPORT.md)
- [参考点表](参考.csv)
- [标准点表](标准.csv)

---

**文档版本**：v1.0  
**最后更新**：2026-01-XX  
**状态**：待执行

---

**文档结束**

# 制冷系统统一设计说明

**用途**：设计、代码接口和点位的统一入口。本文档合并 ARCHITECTURE_PLAN.md、SYSTEM_LOGIC_DESIGN.md、THROTTLE_DESIGN.md 的有效设计内容。
**核对日期**：2026-09-08  
**版本**：v3.3（修订转速环设计：外环输出油门，转速环作为保护限幅）

---

## 文档说明

本文档是**唯一的设计规范入口**，合并以下历史文档的有效内容：

- `ARCHITECTURE_PLAN.md` v2.5：架构设计基准、保护规则（§9）、系统主逻辑（§16）
- `SYSTEM_LOGIC_DESIGN.md`：已合并至本文档，保留为历史详细设计稿
- `THROTTLE_DESIGN.md`：已合并至本文档，保留为历史变更记录

历史文档中标记"已合并"的内容以本文档为准。代码实施时必须遵守本文档的约定。

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

### 1.3 系统类型

- **制冷模式**：风冷直膨（适配冷水）
- **被控变量**：出液温度（载冷剂出口温度，送风/出水温度）
- **控制目标**：`setpoint = 22.0°C`（Modbus hr17 可调）
- **不做除霜与制热**：硬件无四通阀，按纯制冷设计

### 1.4 代码完成度

- **Step1-4**：已封板（检查层、Alarm/supervisor、Driver link 去阻塞）
- **Step5a**：已封板（设备执行层、转速环架构 v3.3、手动模式、启停时序）
- **Step5b**：已封板（三环控制、温控循环、L1 限载、排气温度保护、重试锁定）
- **Step6**：待开始（参数统一、Modbus 扩展、周期失配修复）
- **测试状态**：161/161 PASS（Step2 25 / Step3 30 / Step4 23 / Step5a 27 / Step5b 56）

**注意**：本文档区分"代码现状"和"目标设计"，不得把伪代码或计划项当作已实现功能。

---

## 2. 硬件资源与平台

### 2.1 硬件资源分配表

| 资源 | 用途 | 引脚/参数 | 状态 |
|---|---|---|---|
| UART1 | 组态屏（测试方案，A5 5A 协议） | 115200 | 运行中（Desplay.py） |
| UART6 | Pico ESC 链路（Driver.py） | 921600 | 已配置 |
| UART7 | EXV TMC2209 | 115200 | 已配置 |
| UART8 | Modbus RTU 从机（正式方案） | 9600，RS485 转接板 | 已配置 |
| SPI1 | AD7124（5×PT100） | CS=PG15 | 正常 |
| TIM4 CH2 | TMC2209 STEP 脉冲 | PD13 | 正常 |
| GPIO | EXV DIR/EN | PE10 / PE12 | 正常 |
| ADC | 压力×2（PC2/PC3，adc_direct 16-bit）、电流（PA4，pyb.ADC 12-bit）、备用（PA5） | — | PC2/PC3 已跑通；PA4 采集代码已实现 |

### 2.2 传感器配置

- **5×PT100**（AD7124，SPI1）：吸气温度、排气温度、进液温度、出液温度、液管温度
- **2×压力传感器**（ADC PC2/PC3）：硬件为表压型探头，**软件全程绝对压力 (bara)**
- **1×整机电流**（ADC PA4）：ACS712-50A，量程 0-50A

**压力软件量全程绝对压力 (bara)**：`psi_trans` 已做表压→绝压（+大气压）；state / validate / Alarm / Modbus 阈值一律绝压，不得再当表压用。

---

## 3. 数据接口

### 3.1 传感器索引

`sensor_data[0..9]` 是原始显示值，故障哨兵 `999` 原样保留；控制逻辑只能读取 `sensor_checked`，并先检查 `sensor_quality == Q_OK` 和 `sensor_age_ms()`。

| 索引 | 常量 | 含义 | 单位 |
|---:|---|---|---|
| 0 | `IDX_SUCTION_T` | 吸气温度 | °C |
| 1 | `IDX_DISCHARGE_T` | 排气温度 | °C |
| 2 | `IDX_INLET_T` | 进液温度 | °C |
| 3 | `IDX_OUTLET_T` | **出液温度，容量环被控变量** | °C |
| 4 | `IDX_LIQUID_T` | 液管温度 | °C |
| 5 | `IDX_SUCTION_PSI` | 吸气压力 | bara |
| 6 | `IDX_DISCHARGE_PSI` | 排气压力 | bara |
| 7 | `IDX_SUPERHEAT` | 过热度（派生） | K |
| 8 | `IDX_E_VALUE` | 焓值（派生） | kJ/kg |
| 9 | `IDX_CURRENT` | 整机总电流 | A |

### 3.2 状态索引 (state_data)

`state_data[]` 长度 32（Step5a 扩展 16→32）：

**设备执行机构状态（0-15）**：

| 索引 | 常量 | 含义 | 单位 | 写者 |
|---:|---|---|---|---|
| 0 | `ST_MOTOR_SPEED` | 压缩机转速反馈 | r/min | Compressor 模块 |
| 1 | `ST_VALVE_POS` | EXV 开度 | % | Valve_CRTL |
| 2 | `ST_VALVE_POS_STEPS` | EXV 步数 | steps | Valve_CRTL |
| 3 | `ST_VALVE_FAULT` | EXV 故障事件位 | 0/1 | EXV 层 |
| 4 | `ST_EVAP_FAN_SPEED_CMD` | 蒸发风机用户设定（hr13） | r/min | Modbus |
| 5 | `ST_EVAP_FAN_RPM` | 蒸发风机转速反馈 | r/min | Evaporator 模块 |
| 6 | `ST_EVAP_FAN_FAULT` | 蒸发风机故障事件位 | 0/1 | Evaporator 模块 |
| 7 | `ST_COND_FAN_SPEED_CMD` | 冷凝风机转速指令 | r/min | Condenser 模块 |
| 8 | `ST_COND_FAN_RPM` | 冷凝风机转速反馈 | r/min | Condenser 模块 |
| 9 | `ST_COND_FAN_FAULT` | 冷凝风机故障事件位 | 0/1 | Condenser 模块 |
| 10 | `ST_SENSOR_FAULT` | 传感器故障汇总（单 bit，Step6 扩展为 16-bit） | 0/1 | supervisor |
| 11 | `ST_SYSTEM_ENABLE` | 系统使能（Modbus hr0） | 0/1 | Modbus |
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

**设计更新（v3.3）**：
- **控制架构**：外环（容量环/压力环/用户设定）输出油门绝对值 → 转速环（可选保护层）限幅调节 → 最终油门输出
- **STARTUP 时期**：supervisor 直接输出 `*_throttle_cmd` 恒定油门（如 `COMP_START_THROTTLE = 300`）
- **RUNNING 时期**：
  - 容量环（外环）输出 `comp_throttle_base`（油门绝对值 48-2047）
  - 冷凝压力环输出 `cond_fan_throttle_base`（油门绝对值 48-2047）
  - 蒸发风机用户设定（hr13）输出 `evap_fan_throttle_base`（油门绝对值 48-2047）
- **转速环（可选保护层）**：
  - 作用：防止转速超限的保护限幅器，不是主控制环
  - 输入：当前转速反馈 `rpm_current` 和最大转速限制 `rpm_max`
  - 输出：油门调节量 `throttle_adjust`（范围 ±100）
  - 融合：`throttle_final = throttle_base + throttle_adjust`
  - 独立开关：`COMP_SPEED_LOOP_ENABLE` / `COND_FAN_SPEED_LOOP_ENABLE` / `EVAP_FAN_SPEED_LOOP_ENABLE`
  - 手动模式下转速环仍然工作（如果开关启用）

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

- 低 16 位映射现有 FC02 离散输入（di0-di15）
- 高 16 位（当前已定义位 16 `FLOW_FAULT`）通过扩展输入寄存器映射
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

### 4.2 控制架构（v3.3）

**外环（慢环）**：输出油门绝对值（throttle_base）
- 容量环（10s）：出液温度 → 压缩机油门（throttle_base）
- 冷凝压力环（2s）：排气压力 → 冷凝风机油门（throttle_base）
- 过热度环（1s）：过热度 → EXV 开度
- 蒸发风机：用户设定（hr13）→ 油门（throttle_base）

**转速环（可选保护层，0.5s）**：输出油门调节量（throttle_adjust）
- 压缩机转速环：rpm_current vs rpm_max → throttle_adjust（±100）
- 冷凝风机转速环：rpm_current vs rpm_max → throttle_adjust（±100）
- 蒸发风机转速环：rpm_current vs rpm_max → throttle_adjust（±100）
- 三个独立开关控制，可单独启用/禁用

**控制流**：
```
外环（温度/压力） → throttle_base（油门绝对值）
                        ↓
转速环（可选）→ throttle_adjust（调节量）
                        ↓
            throttle_final = throttle_base + throttle_adjust → ESC
                        ↑
                   ESC 遥测反馈 rpm
```

**关键设计**：
- 外环直接输出油门绝对值（48-2047），基于启动默认油门累加 PID 输出
- 转速环作为保护限幅器，仅在转速接近上限时产生负调节量
- 转速环死区 200 rpm，最大调节量 ±100 throttle
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

**C8 问题（Step6 待修复）**：
- **当前状态**：`ST_SENSOR_FAULT`（索引 10）为单 bit，仅区分"有故障"和"无故障"
- **数据源**：`sensor_fault_reg` 已是 16-bit 寄存器（5 路传感器 + 2 路压力 + 1 路电流 = 8 位有效）
- **问题**：信息丢失，无法区分具体哪路传感器故障
- **Step6 修复方案**：
  - 保留 `ST_SENSOR_FAULT` 作为汇总标志（向后兼容）
  - 新增 Modbus `ir12`：映射完整 16-bit `sensor_fault_reg`
  - 新增 Discrete Inputs `di16-di23`：分位映射各传感器故障（可选）
  - 位定义顺序：bit0=吸气温度、bit1=排气温度、bit2=进液温度、bit3=出液温度、bit4=液管温度、bit5=高压、bit6=低压、bit7=电流、bit8-15=保留

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
| S1 | 冷凝风机目标转速 → `COND_FAN_START_RPM` | 反馈转速有效（`ST_ESC2_RPM` > 0） | 延时确认后告警/停机 |
| S2 | 蒸发风机目标转速 → `EVAP_FAN_START_RPM` | 反馈转速有效（`ST_ESC1_RPM` > 0） | 延时确认后告警/停机 |
| S3 | EXV 到固定开度 `EXV_START_POS`（% 或步数） | 到位且无 EXV_FAULT | 延时确认后 → SAFE_STOP |
| S4 | **EXV 开始动作后延时** `delay_slots[EXV_TO_COMP_DELAY_SLOT]` 秒，再启动压缩机软启斜坡 | 软启斜坡结束且反馈转速有效 | 延时确认后 → SAFE_STOP |
| S5 | 系统层晋级（二选一，hr59 可配）| 晋级条件满足 | 超时 → SAFE_STOP |
| S6 | 进入 RUNNING；EXV 过热度环使能；各保护武装 | sys_mode=RUNNING | — |

**启动时油门控制（v3.3 修订）**：
- **S1/S2**：supervisor 输出固定 throttle_base（`COND_FAN_START_THROTTLE` / `EVAP_FAN_START_THROTTLE`），设备模块内转速环（若启用）提供保护限幅
- **S4 软启**：压缩机以恒定油门启动（`COMP_START_THROTTLE`），无斜坡，直接输出固定值
- **转速环**：STARTUP 期间若启用，仅提供超速保护，不参与主动控制

**启动两层（不冲突）**：

| 层 | 职责 | 谁实现 |
|---|---|---|
| **压缩机启动** | 输出恒定启动油门：`COMP_START_THROTTLE` | `Alarm/supervisor` |
| **系统晋级 S5** | 用转速稳定性判定"可以进入 RUNNING" | `Alarm/supervisor` |

**S5 晋级模式** `STARTUP_ADVANCE_MODE`（hr59 可写）：

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
- 转速环在 STARTUP 期间若启用，仅作为超速保护，不参与主动控制

### 5.3 正常停机时序（N0-N4）

| 步 | t= | 动作 |
|---|---|---|
| N0 | 0 | 接受停机请求 |
| N1 | 0 | EXV 开始关闭（关至安全位/归零） |
| N2 | N1+`delay_slots[COMP_STOP_DELAY_SLOT]` | 压缩机 ESC0：throttle→0 并 disable |
| N3 | N2+`delay_slots[FAN_STOP_DELAY_SLOT]` | 蒸发风机、冷凝风机停 |
| N4 | 全部完成 | sys_mode=SAFE_STOP；全 ESC disable 确认 |

默认延时约 3s（槽 1），用户可在屏幕指定槽号。

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
            → 操作者在触摸屏点"故障复位"（hr48 写 1 脉冲）
            → supervisor 对该位尝试 clear
            → 下一周期重新跑 Alarm 判定：
                 条件已消失 → 位保持正常(1)，允许再次 STARTUP
                 条件仍在   → 立即再次置位异常(0)
```

- **清故障唯一入口**：触摸屏手动复位（hr48 Modbus）
- 程序在复位之后**只负责再判定**
- `ST_SYSTEM_ENABLE` 与故障复位分离：复位不等于开机

---

## 6. 报警与保护策略

### 6.1 报警分级

| 级 | 名称 | 语义 | 动作 | error_reg | 复位 |
|---|---|---|---|---|---|
| **L0** | 告警 | 仅提示，系统照常运行 | 无 | 置位 | 条件消失自动清 |
| **L1** | 限载 | 抑制工况恶化，不停机 | 禁升速/降容/冷凝风全开/关小 EXV | 置位 | 条件消失自动清 |
| **L2** | 延时停机 | 保护性停机，可重启 | 延时到点 → §5.4 故障停机 | **锁存** | 手动复位（hr48） |
| **L3** | 锁定停机 | 反复故障，禁止自动重试 | 同 L2 + 置 `retry_lockout` | **锁存** | 手动复位 + 清重试计数 |

L1 是关键层：同一物理量按严重度设多级阈值，**先自保（限载/降容）再跳闸**。

### 6.2 动作码

| 码 | 名称 | 含义 | 状态 |
|---|---|---|---|
| 0 | `ACT_NONE` | 仅告警 | 已实现 |
| 1 | `ACT_SAFE_STOP` | 故障停机 §5.4 | 已实现 |
| 2 | `ACT_EXV_REDUCE` | 关小 EXV，不停机 | 已实现 |
| 3 | `ACT_INHIBIT_UP` | 禁升速：容量环只许降不许升 | 待实现 Step5b |
| 4 | `ACT_UNLOAD` | 降容：按 `UNLOAD_RATE_RPM_S` 强制降压缩机转速 | 待实现 Step5b |
| 5 | `ACT_COND_FAN_MAX` | 冷凝风机强制全速排热 | 待实现 Step5b |
| 6 | `ACT_NORMAL_STOP` | 正常停机 §5.3（不锁存 error_reg） | 待实现 Step5b |

### 6.3 分级阈值表

同一物理量的多级阈值必须满足 **warn < unload < trip** 的单调关系，supervisor 启动时校验，违反则拒绝启动并置 `CONFIG_IO_FAULT`。

| 物理量 | L0 告警 | L1 限载 | L2/L3 跳闸 |
|---|---|---|---|
| 排气压力 | `HP_WARN` 19.0 bara | `HP_UNLOAD` 20.5 bara → 降容 + 冷凝风全开 | `HP_MAX` 22.0 bara → 停机 |
| 整机电流 | `I_WARN` 38.0 A | `I_LIMIT` 40.0 A → 禁升速；`I_UNLOAD` 42.0 A → 降容 | `I_MAX` 45.0 A → 停机 |
| 排气温度 | `TD_WARN` 95.0°C | `TD_UNLOAD` 105.0°C → 降容（不低于回油转速） | `TD_TRIP` 115.0°C → 持续 300s 停机 |
| 吸气压力 | `LP_WARN` 3.0 bara | `LP_UNLOAD` 2.8 bara → 降容 | `LP_MIN` 2.5 bara → 停机 |
| 过热度 | — | `SH_MIN` 2.0 K → 关小 EXV；`SH_CRIT` 1.0 K → 降容 | 不跳闸 |

### 6.4 延时槽设计

8 个共享延时槽：`delay_slots[0..7] = [0, 5, 10, 30, 45, 60, 300, 900]`（秒）

| 槽号 | 默认值 | 用途 |
|---|---|---|
| 0 | 0 | 即时动作 |
| 1 | 5 | 传感器 BAD / EXV 故障 / 风机故障 / **启停时序短延时** |
| 2 | 10 | 低压 / 吸气压力传感器 BAD |
| 3 | 30 | 预留 |
| 4 | 45 | 预留 |
| 5 | 60 | Modbus 通讯断延时 |
| 6 | 300 | 排气温度二阶停机（5 min 持续高温） |
| 7 | 900 | 预留长延时 |

### 6.5 完整规则表（L2 已实现 + L1 待实现）

**已实现（Step3）**：

| 规则 | error_reg 位 | 条件 | 延时槽 | 级 | 动作 |
|---|---|---|---|---|---|
| 高压保护 | 4 | 排气压力 > `HP_MAX` | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 排气压力传感器 BAD | 13 | quality BAD | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 低压保护 | 5 | 吸气压力 < `LP_MIN` | 槽 2 (10s) | L2 | 锁存 + §5.4；**仅 RUNNING** |
| 吸气压力传感器 BAD | 12 | quality BAD | 槽 2 (10s) | L2 | 锁存 + §5.4；**仅 RUNNING** |
| 低过热度 | 10 | SH < `SH_MIN` | 槽 1 (5s) | L1 | 关小 EXV，不锁存 |
| 过流 | 7 | 电流 > `I_MAX` | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 电流传感器 BAD | 14 | quality BAD | 槽 1 (5s) | L0/L2 | 按 `CURRENT_BAD_ACTION` |
| Driver 链路断 | 软标志 | link_ok 连续假 | 槽 1 (5s) | L2 | 锁存 + 全 ESC 禁用 |
| EXV 故障 | 0 | `ST_VALVE_FAULT` 事件位 | 槽 1 (5s) | L2 | 锁存 + §5.4 |
| 传感器全超龄 | 15 | 全 10 通道最小年龄 > `AGE_LIMIT_MS` | 槽 1 (5s) | L2 | 锁存 + §5.4 |

**L1 限载规则（Step5b 实现）**：

| 规则 | 条件 | 延时槽 | 动作 | 锁存 |
|---|---|---|---|---|
| 高压限载 | 排气压力 > `HP_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_COND_FAN_MAX` | 否 |
| 电流禁升 | 电流 > `I_LIMIT` | 槽 1 (5s) | `ACT_INHIBIT_UP` | 否 |
| 电流降容 | 电流 > `I_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 低压限载 | 吸气压力 < `LP_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 临界低过热 | SH < `SH_CRIT` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_EXV_REDUCE` | 否 |
| 排气温度降容 | 排气温度 > `TD_UNLOAD` | 槽 1 (5s) | `ACT_UNLOAD` | 否 |
| 排气温度停机 | 排气温度 > `TD_TRIP` 连续 | 槽 6 (300s) | `ACT_SAFE_STOP` 锁存 | 是 |

**L2 新增规则（Step5a/5b）**：

| 规则 | error_reg 位 | 条件 | 延时槽 | 归属 |
|---|---|---|---|---|
| 压缩机故障 | 1 | 使能且有油门指令，但反馈转速 `ST_ESC0_RPM` ≈0（ESC 保护或电机异常） | 槽 1 (5s) | Step5a |
| 蒸发风机故障 | 2 | 使能且有油门指令，但反馈转速 `ST_ESC1_RPM` 偏离目标 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a |
| 冷凝风机故障 | 3 | 使能且有油门指令，但反馈转速 `ST_ESC2_RPM` 偏离目标 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a |
| Modbus 通讯断 | 9 | 无有效 Modbus 帧超 `MODBUS_TIMEOUT_S` | 槽 5 (60s) | Step5b |
| 流量故障 | 16 | 水流开关 DI 断开 | 槽 2 (10s) | Step5a |

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

### 7.2 容量环（出液温度 → 压缩机油门）

**控制架构（v3.3 修订）**：

```
外环（容量环，10s）：出液温度 → throttle_base（基础油门，绝对值）
                         ↓
转速环（可选，0.5s）：rpm_current vs rpm_max → throttle_adjust（调节量）
                         ↓
                 throttle_final = throttle_base + throttle_adjust → ESC0
                         ↑
                    ESC0 遥测反馈 rpm
```

#### 7.2.1 外环：容量环（温度 PID）

**输入**：`sensor_checked[IDX_OUTLET_T]`（出液温度，载冷剂出口）  
**输出**：`comp_throttle_base`（压缩机基础油门，绝对值 48-2047）  
**目标**：`compressor_control_params["setpoint"] = 22.0°C`（Modbus hr17 可调）  
**周期**：10s

**PID 参数**（`compressor_control_params`）：

```python
{
    'Kp': 50.0,             # 比例系数：温度偏差 1K → 油门变化 50
    'Ki': 5.0,              # 积分系数
    'Kd': 10.0,             # 微分系数
    'setpoint': 22.0,       # 目标出液温度 °C
    'deadband': 0.5,        # 死区 ±0.5K
    'throttle_min': 48,     # 最小油门（可调参数）
    'throttle_max': 2047,   # 最大油门（可调参数）
    'max_delta': 200,       # 单次最大变化 200 throttle/10s
}
```

**控制流程**：
1. 读取出液温度实时值，检查 quality 与 age
2. 计算偏差：`error = setpoint - 实时值`
3. PID 运算输出油门变化量（基于启动默认油门累加）
4. L1 限载覆盖（P2 优先级）：
   - `ACT_INHIBIT_UP`：禁止油门继续增加
   - `ACT_UNLOAD`：按 `UNLOAD_RATE_THROTTLE_S` 强制降到 `COMP_OIL_RETURN_THROTTLE`
5. 限幅：`[throttle_min, throttle_max]`
6. 斜坡限速：升速/降速速率受 `max_delta` 限制
7. 输出：`comp_throttle_base`（油门绝对值）

**质量门禁**：quality 非 OK 或 age > 2s → 保持当前 throttle_base，冻结积分。

#### 7.2.2 转速环：转速保护限幅（可选）

**输入**：`comp_throttle_base`（基础油门）+ `state_data[ST_ESC0_RPM]`（反馈转速）  
**输出**：`comp_throttle_adjust`（调节量，±100 范围）  
**最终输出**：`comp_throttle_cmd = comp_throttle_base + comp_throttle_adjust`  
**周期**：0.5s（持续运行）
**使能开关**：`COMP_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`comp_speed_loop_params`）：

```python
{
    'Kp': 0.5,                  # throttle/rpm（待台架标定）
    'Ki': 0.1,                  # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 6000,            # 最大转速限制
    'deadband': 200,            # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100, # 最大调节量 ±100
}
```

**控制流程**：
1. 检查 `COMP_SPEED_LOOP_ENABLE`，若禁用则跳过，`throttle_adjust = 0`
2. 读取 `state_data[ST_ESC0_RPM]`（ESC 遥测反馈）
3. 计算偏差：`error = rpm_current - rpm_max`（当前转速 - 最大转速）
4. 如果 `abs(error) < deadband`：不调节，`throttle_adjust = 0`
5. 如果 `error >= deadband`（超速）：PID 运算输出负调节量（降低油门）
6. 限幅：`throttle_adjust` 限制在 `[-100, +100]`
7. 融合：`throttle_cmd = comp_throttle_base + throttle_adjust`
8. 最终限幅：`max(0, min(2047, throttle_cmd))`（停机时强制 0）
9. 发送给 Driver ESC0

**质量门禁**：反馈 rpm 无效（999）或 driver_link_ok=False → `throttle_adjust = 0`（不调节）。

**实现位置**：`Compressor/control.py`；公共接口由 `Compressor/__init__.py` 导出（Step5b 实现）

### 7.3 冷凝压力环（排气压力 → 冷凝风机油门）

**控制架构（v3.3 修订）**：

```
外环（压力环，2s）：排气压力 → throttle_base（基础油门，绝对值）
                       ↓
转速环（可选，0.5s）：rpm_current vs rpm_max → throttle_adjust（调节量）
                       ↓
               throttle_final = throttle_base + throttle_adjust → ESC2
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

#### 7.3.2 转速环：转速保护限幅（可选）

**输入**：`cond_fan_throttle_base`（基础油门）+ `state_data[ST_ESC2_RPM]`（反馈转速）  
**输出**：`cond_fan_throttle_adjust`（调节量，±100 范围）  
**最终输出**：`cond_fan_throttle_cmd = cond_fan_throttle_base + cond_fan_throttle_adjust`  
**周期**：0.5s（持续运行）
**使能开关**：`COND_FAN_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`fan_speed_loop_params`）：

```python
{
    'Kp': 0.3,                  # throttle/rpm（待台架标定）
    'Ki': 0.05,                 # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 3000,            # 最大转速限制
    'deadband': 200,            # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100, # 最大调节量 ±100
}
```

**控制流程**：
1. 检查 `COND_FAN_SPEED_LOOP_ENABLE`，若禁用则跳过，`throttle_adjust = 0`
2. 读取 `state_data[ST_ESC2_RPM]`（ESC 遥测反馈）
3. 计算偏差：`error = rpm_current - rpm_max`（当前转速 - 最大转速）
4. 如果 `abs(error) < deadband`：不调节，`throttle_adjust = 0`
5. 如果 `error >= deadband`（超速）：PID 运算输出负调节量（降低油门）
6. 限幅：`throttle_adjust` 限制在 `[-100, +100]`
7. 融合：`throttle_cmd = cond_fan_throttle_base + throttle_adjust`
8. 最终限幅：`max(0, min(2047, throttle_cmd))`（停机时强制 0）
9. 发送给 Driver ESC2

**质量门禁**：反馈 rpm 无效（999）或 driver_link_ok=False → `throttle_adjust = 0`（不调节）。

**实现位置**：`Condenser/control.py`；公共接口由 `Condenser/__init__.py` 导出（Step5b 实现）

### 7.4 过热度环（过热度 → EXV 开度）

**输入**：`sensor_checked[IDX_SUPERHEAT]`（过热度，K）  
**输出**：EXV 开度增量  
**目标**：`control_params["setpoint"] = 5.0 K`（Modbus hr4 可调）  
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

### 7.5 蒸发风机（用户手动设定 + 转速环保护）

**控制架构（v3.3 修订）**：

```
用户设定（hr13）→ throttle_base（基础油门，绝对值）
                       ↓
转速环（可选，0.5s）：rpm_current vs rpm_max → throttle_adjust（调节量）
                       ↓
               throttle_final = throttle_base + throttle_adjust → ESC1
                       ↑
                  ESC1 遥测反馈 rpm
```

**策略**：RUNNING 期间始终读取 `state_data[ST_EVAP_FAN_SPEED_CMD]`（对应 Modbus hr13），无自动调节逻辑。

**实现**：`Evaporator/control.py`；公共接口由 `Evaporator/__init__.py` 导出（Step5b 实现）

#### 7.5.1 基础油门来源

- **STARTUP**：服从 `supervisor_interlocks["evap_fan_throttle_base"]`（固定值 `EVAP_FAN_START_THROTTLE`）
- **RUNNING**：读取 hr13 用户设定值（默认 1500，油门绝对值）

#### 7.5.2 转速环：转速保护限幅（可选）

**输入**：`evap_fan_throttle_base`（基础油门）+ `state_data[ST_ESC1_RPM]`（反馈转速）  
**输出**：`evap_fan_throttle_adjust`（调节量，±100 范围）  
**最终输出**：`evap_fan_throttle_cmd = evap_fan_throttle_base + evap_fan_throttle_adjust`  
**周期**：0.5s（持续运行）
**使能开关**：`EVAP_FAN_SPEED_LOOP_ENABLE`（0=禁用，1=启用）

**PID 参数**（`fan_speed_loop_params`，与冷凝风机共享）：

```python
{
    'Kp': 0.3,                  # throttle/rpm（待台架标定）
    'Ki': 0.05,                 # 积分系数
    'Kd': 0.0,                  # 微分系数（可选）
    'rpm_max': 3000,            # 最大转速限制
    'deadband': 200,            # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100, # 最大调节量 ±100
}
```

**控制流程**：
1. 检查 `EVAP_FAN_SPEED_LOOP_ENABLE`，若禁用则跳过，`throttle_adjust = 0`
2. 读取 `state_data[ST_ESC1_RPM]`（ESC 遥测反馈）
3. 计算偏差：`error = rpm_current - rpm_max`（当前转速 - 最大转速）
4. 如果 `abs(error) < deadband`：不调节，`throttle_adjust = 0`
5. 如果 `error >= deadband`（超速）：PID 运算输出负调节量（降低油门）
6. 限幅：`throttle_adjust` 限制在 `[-100, +100]`
7. 融合：`throttle_cmd = evap_fan_throttle_base + throttle_adjust`
8. 最终限幅：`max(0, min(2047, throttle_cmd))`（停机时强制 0）
9. 发送给 Driver ESC1

**质量门禁**：反馈 rpm 无效（999）或 driver_link_ok=False → `throttle_adjust = 0`（不调节）。

### 7.6 温控循环（R2 ↔ R4）

**R1/R2 → R4**：达到设定且最短运行时间满足时：
- 条件：出液温度 <= setpoint 且压缩机转速 <= rpm_min
- 延时：`MIN_ON_S` = 180s
- 动作：进入 R4 THERMO_OFF，压缩机停、EXV 关至 10%、冷凝风机停、蒸发风机可选保持

**R4 → R1**：回温超回差且最短停机时间满足时：
- 条件：出液温度 > (setpoint + `THERMO_HYST`)
- 延时：`MIN_OFF_S` = 300s
- 动作：退出 R4，重启压缩机，进入 R1 PULLDOWN

---

## 8. 手动控制模式

### 8.1 手动模式参数（v3.3 修订）

| hr | 名称 | 类型 | 说明 | 默认 |
|---|---|---|---|---|
| hr9 | `MANUAL_MODE_ENABLE` | u16 | 0=自动 / 1=手动（控制压缩机/冷凝风机/EXV） | 0 |
| hr11 | `MANUAL_COMP_THROTTLE` | u16 | 手动压缩机油门 48-2047 | 0 |
| hr12 | `MANUAL_COND_FAN_THROTTLE` | u16 | 手动冷凝风机油门 48-2047 | 0 |
| hr13 | `EVAP_FAN_USER_THROTTLE` | u16 | 蒸发风机油门 48-2047（RUNNING 期间始终生效） | 1500 |
| hr14 | `MANUAL_EXV_PCT` | i16 | 手动 EXV 开度 0-100 (0.1倍率) | 300 (30%) |

**设计变更（v3.3）**：
- hr11/12/13 为**油门绝对值（throttle）**，范围 48-2047
- 手动模式下，转速环仍然工作（如果对应开关启用），提供超速保护
- 这样手动模式与自动模式使用相同的转速环保护机制

**说明**：
- hr9=1 时，压缩机、冷凝风机、EXV 受 hr11/12/14 控制，容量环和冷凝压力环输出无效
- hr13 蒸发风机在 RUNNING 期间**始终生效**，不受 hr9 控制
- 手动模式仍受保护逻辑约束（高压/过流等仍触发停机）
- 手动模式下转速环仍然生效（根据对应开关），防止手动油门过大导致超速

### 8.2 手动模式安全约束

- 手动转速仍受 `[ST_MOTOR_SPEED_MIN, ST_MOTOR_SPEED_MAX]` 限幅
- 从 0 启动时仍需斜坡，首次启动不超过 `COMP_START_THROTTLE`
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
| **限速参数** | `ST_MOTOR_SPEED_MIN/MAX` | ✅ | ✅ | 立即（影响输出限幅） |
| **ESC 倍率** | `ESC_TELEMETRY_CFG` | ✅ | ✅ | 立即（遥测换算） |
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
    "COMP_START_THROTTLE": 300,              # 压缩机启动油门（恒定值）
    "COND_FAN_START_THROTTLE": 500,          # 冷凝风机启动油门
    "EVAP_FAN_START_THROTTLE": 400,          # 蒸发风机启动油门
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
    "FLOW_ENABLE": 0,                        # 流量检查使能（当前无流量计）
    "CURRENT_BAD_ACTION": 0,                 # 电流传感器 BAD：0=禁升速，1=停机
    "LINK_CHECK_ENABLE": 0,                  # 链路检测使能
}

# 容量环 PID（外环，温度→油门）v3.3 修订
compressor_control_params = {
    'Kp': 50.0,                    # 比例系数：温度偏差 1K → 油门变化 50
    'Ki': 5.0,                     # 积分系数
    'Kd': 10.0,                    # 微分系数
    'setpoint': 22.0,              # 出液温度目标 °C
    'deadband': 0.5,               # 死区 ±0.5K
    'throttle_min': 48,            # 最小油门（可调参数）
    'throttle_max': 2047,          # 最大油门（可调参数）
    'max_delta': 200,              # 单次最大变化 200 throttle/10s
}

# 压缩机转速环 PID（保护限幅，转速→油门调节量）v3.3 修订
comp_speed_loop_params = {
    'Kp': 0.5,                     # throttle/rpm（待台架标定）
    'Ki': 0.1,                     # 积分系数
    'Kd': 0.0,                     # 微分系数
    'period_s': 0.5,               # 0.5s 周期
    'rpm_max': 6000,               # 最大转速限制
    'deadband': 200,               # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100,    # 最大调节量 ±100
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

# 风机转速环 PID（保护限幅，转速→油门调节量）v3.3 修订
fan_speed_loop_params = {
    'Kp': 0.3,                     # throttle/rpm（待台架标定）
    'Ki': 0.05,                    # 积分系数
    'Kd': 0.0,                     # 微分系数
    'period_s': 0.5,               # 0.5s 周期
    'rpm_max': 3000,               # 最大转速限制
    'deadband': 200,               # ±200 rpm 死区（进入死区开始调节）
    'throttle_max_adjust': 100,    # 最大调节量 ±100
}

# 手动模式（v3.3 改为油门）
manual_params = {
    'MANUAL_MODE_ENABLE': 0,           # 0=自动 / 1=手动
    'MANUAL_COMP_THROTTLE': 0,         # 手动压缩机油门 48-2047
    'MANUAL_COND_FAN_THROTTLE': 0,     # 手动冷凝风机油门 48-2047
    'EVAP_FAN_USER_THROTTLE': 1500,    # 蒸发风机油门（RUNNING 期间始终生效）
    'MANUAL_EXV_PCT': 300,             # 手动 EXV 开度 0-1000（0.1倍率，30%）
}
```

### 9.3 ESC 遥测配置

```python
# ESC 遥测配置
ESC_TELEMETRY_CFG = [
    # ESC0 压缩机
    {"erpm": True,  "temp": True,  "voltage": True,  "current": True,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 4},
    # ESC1 蒸发风机 — 本机 current 无效
    {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 7},
    # ESC2 冷凝风机 — 本机 current 无效
    {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 7},
    # ESC3 备用
    {"erpm": False, "temp": False, "voltage": False, "current": False,
     "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 7},
]
```

**修复说明（CRITICAL-1，Step5a 已修复）**：
- voltage_scale/current_scale 从 0.01 修正为 1.0
- 原错误倍率导致反馈值缩小 100 倍
- 修复位置：`state.py:410,413,416,419`

### 9.4 参数持久化规范（Step6 实现）

#### 9.4.1 持久化范围

**需要持久化的参数**：
- 所有 Modbus Holding Registers（hr0-hr92）
- PID 参数：过热度环、容量环、冷凝压力环、转速环
- 保护阈值：高压、低压、电流、排气温度、过热度
- 延时槽：8 个槽值
- 启动参数、防短循环参数、转速环开关

**不持久化的参数**：
- 手动模式指令（hr11-hr14）：运行时临时指令
- 故障复位（hr48）：脉冲命令
- 所有只读寄存器（Input Registers）
- 运行时状态变量

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

### 10.1 当前布局（hr0-hr29，ir0-ir9）

当前对外布局以 `MODBUS_POINT_TABLE.md` 为准：
- 系统使能：`hr0`
- 手动油门：`hr11-13`
- 状态输入：`ir0..48`

`Display_Touch/modbus_regs.py` 与 `modbus_slave.py` 必须保持该布局。

### 10.2 扩展寄存器（Step6 实现）

**Holding Registers（可读写）**：

| 地址范围 | 内容 |
|---|---|
| hr0-hr7 | 过热度环 PID 参数（已实现） |
| hr8-hr10 | EXV 控制（预留） |
| hr11-hr13 | 手动模式（压缩机/冷凝风机/蒸发风机） |
| hr14-hr20 | 容量环 PID 参数 |
| hr26-hr29 | 转速限幅 |
| hr30-hr47 | 保护阈值（高压/低压/电流/排温/过热度） |
| hr48 | 故障复位命令（写 1 脉冲） |
| hr49-hr56 | 延时槽 0-7 |
| hr57-hr67 | 启动时序参数 |

**Input Registers（只读）**：

| 地址范围 | 内容 |
|---|---|
| ir0-ir9 | 传感器数据（×100 或 ×10） |
| ir10-ir11 | 质量码 |
| ir12 | 传感器故障寄存器（16-bit） |
| ir13-ir14 | 系统模式 / RUNNING 子状态 |
| ir15-ir16 | 故障寄存器低 16 位 / 高 16 位 |
| ir17-ir25 | 执行机构状态（EXV/压缩机/风机） |
| ir26-ir32 | Supervisor 联锁信号（监控用） |
| ir33-ir48 | ESC 遥测（4 通道 × 4 字段） |

**Discrete Inputs（只读）**：

| 地址范围 | 内容 |
|---|---|
| di0-di15 | error_reg 位镜像（低 16 位） |
| di16-di31 | error_reg 位镜像（高 16 位） |

详细点位表见 `MODBUS_POINT_TABLE.md`。

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
| Q5 | 流量报警位 | **新增 error_reg 位 16** |
| Q6 | 被控变量是什么？ | **出液温度 `IDX_OUTLET_T`** |
| Q7 | `setpoint` 的单位与物理量 | **22.0°C**，送风（出水）温度 |
| Q8 | 系统类型 | **风冷直膨（适配冷水）** |
| Q9 | 是否需要除霜？ | **不做** |
| Q10 | 是否要制热模式？ | **不做**，硬件无四通阀 |
| Q11 | `HP_TARGET` 与 `setpoint` 初值 | **12.0 bara** / **22.0°C** |

### 11.3 时序变更（2026-09-03）

| # | 变更 | 说明 |
|---|---|---|
| 1 | 启动顺序修订 | 冷凝风机（S1）→ 蒸发风机（S2）→ EXV（S3）→ 延时后压缩机（S4） |
| 2 | EXV → 压缩机延时 | 引用 `delay_slots[EXV_TO_COMP_DELAY_SLOT]`，用户可配 |
| 3 | 停机顺序修订 | EXV 关闭 → 延时停压缩机 → 延时停风机 |
| 4 | 废弃固定时间参数 | 改为引用延时槽 |

### 11.4 转速环设计（2026-09-08，v3.3 修订）

**架构变更历史**：
- **v3.2（2026-09-07）**：双环控制，外环输出 rpm_cmd，内环转速环输出 throttle_cmd
- **v3.3（2026-09-08）**：修订为外环输出 throttle，转速环作为可选保护限幅器

| # | 决策 | v3.3 拍板结论 |
|---|---|---|
| 1 | 是否增加转速环？ | **是**，作为可选的超速保护机制（三个独立开关控制） |
| 2 | 转速环实施范围 | **压缩机 + 冷凝风机 + 蒸发风机**（三个执行机构） |
| 3 | 控制架构 | **外环输出油门 + 转速环限幅保护**：外环（温度/压力）→ throttle_base；转速环（可选）→ throttle_adjust；最终 throttle_cmd = throttle_base + throttle_adjust |
| 4 | 转速环角色 | **保护限幅器**，防止转速超过 rpm_max，不是主控制环 |
| 5 | 转速环周期 | **0.5s**（持续运行，但只在接近上限时产生作用） |
| 6 | supervisor 联锁输出 | 保持 `*_throttle_cmd`（油门绝对值），不改为 rpm_cmd |
| 7 | 手动模式参数 | hr11/12/13 为油门值（throttle，48-2047） |
| 8 | 启动时序参数 | `COMP_START_THROTTLE` / `COND_FAN_START_THROTTLE` / `EVAP_FAN_START_THROTTLE`（油门） |
| 9 | 转速环开关 | `COMP_SPEED_LOOP_ENABLE` / `COND_FAN_SPEED_LOOP_ENABLE` / `EVAP_FAN_SPEED_LOOP_ENABLE`（三个独立开关） |
| 10 | 晋级判据 | 转速稳定性检查：10 秒窗口内转速变化 < 100 rpm（模式 1） |
| 11 | 转速环实现位置 | `Compressor/control.py` / `Condenser/control.py` / `Evaporator/control.py`；公共接口由各 `__init__.py` 导出 |

**v3.3 设计理由**：
- 外环直接输出油门更直观，易于调试和手动控制
- 转速环作为保护层，可独立开关，不影响基础控制逻辑
- 启动时以恒定油门启动更简单可靠，无需斜坡
- 晋级判据基于转速稳定性，不依赖目标转速到位
- 手动模式与自动模式使用相同的转速环保护机制

---

## 附录：文档参考

- 进度与问题台账：`PLAN.md`
- 验收报告：`REPORT.md`
- 文件结构与索引：`STRUCTURE.md`
- Modbus 点位表：`MODBUS_POINT_TABLE.md`
- 开发规范：`AI_DEVELOPMENT_RULES.md`

---

**文档结束**

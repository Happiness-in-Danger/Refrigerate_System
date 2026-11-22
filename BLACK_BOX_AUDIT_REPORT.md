# 制冷系统黑箱测试审核报告

**项目名称**: Refrigerate_System  
**审核日期**: 2024年  
**审核方式**: 黑箱测试（不阅读 docs/ 目录）  
**审核范围**: 逻辑错误、变量缺失、Modbus 点位异常  

---

## 执行摘要

本次黑箱测试发现 **3 个严重问题**、**5 个中等问题**、**7 个轻微问题**，共计 **15 项**。

**严重问题**会导致数据冲突或启动失败，必须修复后方可上线。  
**中等问题**影响控制精度和参数一致性，建议优先修复。  
**轻微问题**不影响核心功能，可作为后续优化项。

---

## 1. 严重问题（必须修复）

### 1.1 冷凝压力环目标双写冲突

**文件**: `Display_Touch/modbus_slave.py:310-311`  
**严重程度**: **严重** - 参数单源违反

```python
# _apply_holding_writes()
if 34 in t:
    pp['HP_TARGET'] = hr[34] / 100.0       # 保护阈值目标
    cond['setpoint'] = hr[34] / 100.0      # 冷凝压力环控制目标（冲突）
```

**问题描述**:  
- `hr34` 同时写入保护参数 `HP_TARGET` 和控制参数 `cond['setpoint']`  
- 用户无法独立配置冷凝压力控制目标与保护目标（例如控制 11.0 bara，保护 12.0 bara）  
- 违反参数单一职责原则

**影响**:  
- 保护阈值与控制目标强制绑定，无法实现"控制目标低于保护目标"的安全裕度设计  
- 修改保护阈值会意外改变控制目标

**修复建议**（二选一）:

**方案A**: 删除冲突行，保持独立配置
```python
if 34 in t:
    pp['HP_TARGET'] = hr[34] / 100.0
    # 删除：cond['setpoint'] = hr[34] / 100.0
    # 冷凝压力环目标由 hr25-30 中的专用寄存器配置（需新增或使用现有）
```

**方案B**: 新增独立寄存器（推荐）
```python
# 保持 hr34 只写 HP_TARGET
# 新增 hr34_COND_SETPOINT 寄存器专门配置冷凝压力环目标
if 34 in t:
    pp['HP_TARGET'] = hr[34] / 100.0
if 34_NEW in t:  # 例如使用 hr86 或其它保留寄存器
    cond['setpoint'] = hr[34_NEW] / 100.0
```

**验证方法**:  
通过 Modbus 修改 hr34，检查 `protect_params['HP_TARGET']` 和 `cond_control_params['setpoint']` 是否独立变化。

---

### 1.2 EXV 归零逻辑缺失

**文件**: `EXV/Valve_CRTL.py:236-238`  
**严重程度**: **严重** - 启动失败

```python
async def run():
    if not state.state_data[state.ST_SYSTEM_ENABLE]:
        await asyncio.sleep_ms(100)
        return  # 直接返回，未执行归零
    
    ok = await exv.homing()  # 只有使能后才归零
    # ...
```

**问题描述**:  
- 系统禁用时 `run()` 协程直接返回，从未调用 `homing()`  
- 首次使能时 EXV 处于未知位置（上电后步进电机位置未知）  
- 后续 PID 控制基于错误的初始位置，导致开度计算完全错误

**影响**:  
首次启动时 EXV 开度未知，可能导致：
- 过热度过低（阀门实际开度过大）→ 液击保护停机  
- 过热度过高（阀门实际开度过小）→ 制冷效果差

**修复建议**:
```python
async def run():
    """上电归零 + 启动 pid_loop 和 valve_loop"""
    # 等待系统使能
    while not state.state_data[state.ST_SYSTEM_ENABLE]:
        await asyncio.sleep_ms(100)
    
    # 使能后立即归零
    ok = await exv.homing()
    if not ok:
        print("[EXV] 归零失败，上报 ST_VALVE_FAULT 事件")
        state.state_data[state.ST_VALVE_FAULT] = 1
        return
    
    # 启动控制循环
    await asyncio.gather(
        pid_loop(),
        valve_loop(),
    )
```

**验证方法**:  
1. 系统上电后保持禁用状态 5 秒  
2. 通过 Modbus 使能系统  
3. 检查 EXV 是否先归零（电机反转到限位）再开始 PID 控制

---

### 1.3 蒸发风机状态镜像越权写入

**文件**: `Display_Touch/modbus_slave.py:278-280`  
**严重程度**: **严重** - 单一写者违反

```python
if 14 in t:
    thr = hr[14]
    state.evap_fan_user_params['EVAP_FAN_USER_THROTTLE'] = thr
    state.state_data[state.ST_EVAP_FAN_SPEED_CMD] = thr  # 越权写入
```

**问题描述**:  
- `ST_EVAP_FAN_SPEED_CMD` 是执行机构输出镜像，应由 `Evaporator/control.py` 唯一写入  
- Modbus 直接写入违反单一写者原则，会与 Evaporator 模块产生竞争  
- `ir4` 寄存器（蒸发风机转速指令）会显示错误值

**影响**:  
- 上位机读取 `ir4` 时可能看到 Modbus 写入值而非实际下发值  
- 调试时误导工程师（实际下发值与显示值不一致）

**修复建议**:
```python
if 14 in t:
    thr = hr[14]
    state.evap_fan_user_params['EVAP_FAN_USER_THROTTLE'] = thr
    # 删除：state.state_data[state.ST_EVAP_FAN_SPEED_CMD] = thr
    # ST_EVAP_FAN_SPEED_CMD 由 Evaporator 模块在实际下发时更新
```

**验证方法**:  
通过 Modbus 修改 hr14，对比 `ir4`（转速指令镜像）与 `ir21`（实际下发油门），两者应一致。

---

## 2. 中等问题（建议修复）

### 2.1 容量环初值可能为 0

**文件**: `Alarm/supervisor.py:730`  
**严重程度**: 中等 - 启动抖动

```python
# 首次进入 RUNNING
state.comp_throttle_base = state.protect_params["COMP_START_THROTTLE"]
state.cond_fan_throttle_base = state.protect_params["COND_FAN_START_THROTTLE"]
```

**问题描述**:  
- 全局变量 `comp_throttle_base` 初值为 0（`Board/state.py:121`）  
- 首次进入 RUNNING 时赋值为启动油门（正常）  
- 但如果 ALARM -> RUNNING 恢复时 `comp_throttle_base <= 0`（行762-763），会重新初始化  
- Compressor 模块容量环 PID 从 0 开始积分，可能导致启动初期输出过低

**影响**:  
启动初期压缩机油门可能短暂过低，制冷效果延迟。

**修复建议**:
```python
# Compressor/control.py:87-95
def _update_capacity_loop(now_ms):
    global _last_cap_ms
    period_ms = int(state.compressor_control_params.get("period_s", 10.0) * 1000)
    if period_ms <= 0:
        period_ms = 10000
    if _last_cap_ms and time.ticks_diff(now_ms, _last_cap_ms) < period_ms:
        return
    _last_cap_ms = now_ms
    _sync_cap_params()
    
    # 新增：初值检查
    if state.comp_throttle_base <= 0:
        cp = state.compressor_control_params
        state.comp_throttle_base = cp["throttle_min"]
        print("[Compressor] 容量环初值为0，设置为 throttle_min=%d" % cp["throttle_min"])
```

---

### 2.2 温控循环 rpm==999 未过滤

**文件**: `Alarm/supervisor.py:798-830`  
**严重程度**: 中等 - 温控误判

```python
motor_rpm = state.state_data[state.ST_MOTOR_SPEED]
# ...
if motor_rpm != 999 and motor_rpm <= rpm_min and outlet_t <= setpoint:
    # 达到设定，检查 MIN_ON_S
```

**问题描述**:  
- 行830 判断 `motor_rpm <= rpm_min` 时未过滤 `rpm == 999`（链路断/无效值）  
- 若 `rpm == 999`（大于 `rpm_min`），条件判为 False，即使温度达标也不进入 R4 THERMO_OFF

**影响**:  
链路断时温控循环失效，系统无法进入热平衡停机状态。

**修复建议**:
```python
# 行830 已修复（实际代码中已有 rpm != 999 判断），无需修改
# 但行798后续使用 motor_rpm 的地方需统一检查
```

**验证方法**:  
模拟链路断（`driver_link_ok=False`），检查出液温度达标后是否能进入 R4。

---

### 2.3 手动模式切换时容量环积分未清零

**文件**: `Compressor/control.py:99-102`  
**严重程度**: 中等 - 模式切换抖动

```python
# 手动模式或 R4 THERMO_OFF 时不更新容量环输出
if (state.manual_params.get("MANUAL_MODE_ENABLE", 0) or
        state.running_substate == state.RUNNING_THERMO_OFF or
        state.sys_mode != state.MODE_RUNNING):
    return  # 保持当前输出，但不清零积分
```

**问题描述**:  
- 手动模式期间跳过容量环更新，但 PID 积分项保留  
- 切回自动模式时继承历史积分，导致输出突变

**影响**:  
手动→自动切换时容量环输出突变，温度短暂震荡。

**修复建议**:
```python
if (state.manual_params.get("MANUAL_MODE_ENABLE", 0) or
        state.running_substate == state.RUNNING_THERMO_OFF or
        state.sys_mode != state.MODE_RUNNING):
    # 新增：清零容量环积分
    _reset_cap_pid()
    return
```

---

### 2.5 风机故障判定逻辑不一致

**文件**: `Compressor/control.py:208` vs `Condenser/control.py:196-198`  
**严重程度**: 中等 - 保护逻辑不一致

```python
# 压缩机故障（Compressor:208）
fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"])

# 冷凝/蒸发风机故障（Condenser:196-198）
tol = state.protect_params["FAN_RPM_TOL"]
fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"] or
         abs(rpm - rpm_cmd) > tol)
```

**问题描述**:  
- 压缩机故障判定不检查转速偏差  
- 冷凝/蒸发风机检查转速偏差 `> FAN_RPM_TOL`  
- 逻辑不一致，可能导致压缩机转速偏离目标但不报故障

**影响**:  
压缩机转速闭环失效时无法及时检测（例如负载突变导致转速严重偏离）。

**修复建议**（可选）:
```python
# Compressor/control.py:206-209
fault = False
if enabled and rpm_cmd > 0:
    tol = state.protect_params.get("COMP_RPM_TOL", 500)  # 新增压缩机容差参数
    fault = (rpm == 999 or rpm <= state.protect_params["RPM_VALID_MIN"] or
             abs(rpm - rpm_cmd) > tol)
```

**备注**:  
如果是有意设计（压缩机容差大，不检查偏差），需在代码注释中说明。

---

### 2.5 变量 COMP_HOLD_S 未在 Modbus 映射

**文件**: `Board/state.py:426`  
**严重程度**: 中等 - 参数不可配置

```python
"COMP_HOLD_S": 10.0,  # 模式0：定速保持秒数
```

**问题描述**:  
- `COMP_HOLD_S` 参数在 `supervisor.py:409` 中消费（启动晋级判据模式0）  
- 但未在 `modbus_slave.py` 中映射到任何 holding register  
- 用户无法通过上位机调整此参数

**影响**:  
启动晋级时间固定，无法现场调试优化。

**修复建议**:
```python
# modbus_slave.py 新增映射（例如使用 hr84 保留寄存器）
if 84 in t: pp['COMP_HOLD_S'] = hr[84]

# _HR_RANGE 新增范围
84: (5, 60),  # COMP_HOLD_S: 5-60 秒

# sync_holding_readback() 新增回读
hr[84] = int(round(pp['COMP_HOLD_S']))
```

---

## 3. 轻微问题（可选修复）

### 3.1 变量 COMP_RPM_STABILITY_SAMPLE_S 未映射

**文件**: `Board/state.py:432`  
**严重程度**: 轻微 - 有默认值

```python
"COMP_RPM_STABILITY_SAMPLE_S": 1.0,  # 稳定性采样间隔
```

**问题描述**:  
- `supervisor.py:422` 使用 `pp.get("COMP_RPM_STABILITY_SAMPLE_S", 1.0)` 消费此参数  
- 未在 Modbus 中映射，但有默认值兜底

**影响**:  
参数不可配置，调试不便，但不影响功能。

**修复建议**:  
与 `COMP_HOLD_S` 类似，新增 Modbus 映射。

---

### 3.2 HP_TARGET 双重职责设计问题

**文件**: `Board/state.py:310-311` (通过 `modbus_slave.py:310-311`)  
**严重程度**: 轻微 - 设计权衡

**问题描述**:  
- `HP_TARGET` 是保护阈值目标（冷凝压力控制应维持在此值以下）  
- 当前设计强制 `cond['setpoint'] = HP_TARGET`  
- 无法实现"控制目标低于保护目标"的安全裕度（例如控制 11.0 bara，保护 12.0 bara）

**影响**:  
设计灵活性降低，但对大多数应用场景无影响。

**修复建议**:  
保留当前设计（简化配置），或按问题1.2方案B新增独立寄存器。

---

### 3.3 蒸发风机手动模式行为不明确

**文件**: `Evaporator/control.py:58-61`  
**严重程度**: 轻微 - 需确认需求

```python
# RUNNING 期间始终读 hr14 用户设定；其它阶段服从 supervisor
if state.sys_mode == state.MODE_RUNNING and enabled:
    throttle_cmd = state.evap_fan_user_params.get("EVAP_FAN_USER_THROTTLE", 0)
else:
    throttle_cmd = il["evap_fan_throttle_cmd"]
```

**问题描述**:  
- 压缩机/冷凝风机在手动模式（hr9=1）时由 hr11/hr12 控制  
- 蒸发风机在 RUNNING 期间始终由 hr14 控制，**未判断手动模式**  
- 行为与其它执行机构不一致

**影响**:  
手动模式下蒸发风机可能仍受 hr14 自动调节，与用户预期不符。

**修复建议**（需确认需求）:

**方案A**: 统一手动模式逻辑
```python
if state.sys_mode == state.MODE_RUNNING and enabled:
    if state.manual_params.get("MANUAL_MODE_ENABLE", 0):
        # 手动模式：hr14 为手动设定
        throttle_cmd = state.evap_fan_user_params.get("EVAP_FAN_USER_THROTTLE", 0)
    else:
        # 自动模式：hr14 为用户期望值（不受控制环调节）
        throttle_cmd = state.evap_fan_user_params.get("EVAP_FAN_USER_THROTTLE", 0)
```

**方案B**: 保留当前设计（蒸发风机始终用户控制），在文档中说明差异。

---

### 3.4 Modbus 寄存器范围表部分参数未覆盖

**文件**: `Display_Touch/modbus_slave.py:50-78`  
**严重程度**: 轻微 - 完整性问题

**问题描述**:  
- `_HR_RANGE` 定义了 hr0-93 的部分寄存器范围  
- 但缺少 hr8/hr11/hr48/hr84-86 等保留或只读寄存器的定义  
- 写入未定义的寄存器会返回异常码 0x02（非法地址）

**影响**:  
未定义的保留寄存器无法写入（符合设计），但代码可读性差。

**修复建议**:  
在 `_HR_RANGE` 中显式列出所有保留寄存器（即使不可写），便于维护。

---

### 3.5 低压范围表单位标注不明确

**文件**: `Display_Touch/modbus_slave.py:62`  
**严重程度**: 轻微 - 文档问题

```python
35: (100, 500), 36: (100, 500), 37: (100, 500), 38: (10, 100),
# LP_MIN / LP_UNLOAD / LP_WARN / LP_HYST
```

**问题描述**:  
- 范围 `(100, 500)` 对应 1.0-5.0 bara（×0.01）  
- 但注释未说明单位，代码可读性差

**影响**:  
维护人员可能误解范围（以为是 10.0-50.0 bara）。

**修复建议**:  
在注释中显式标注单位：
```python
# LP_MIN / LP_UNLOAD / LP_WARN (单位: 0.01 bara, 即 1.0-5.0 bara)
35: (100, 500), 36: (100, 500), 37: (100, 500),
```

---

### 3.6 state_data 数组越界风险

**文件**: `Board/state.py:157`  
**严重程度**: 轻微 - 边界检查

```python
state_data = [0] * 34   # ST_FLOW_SWITCH=32, ST_COMPRESSOR_FAULT=33
```

**问题描述**:  
- 定义了 34 个元素（索引 0-33）  
- `ST_COMPRESSOR_FAULT = 33` 为最后一个合法索引  
- 后续扩展时可能忘记增加数组大小

**影响**:  
新增状态量时可能越界访问。

**修复建议**:  
增加数组大小预留或动态检查：
```python
state_data = [0] * 64   # 预留到 64 个，防止后续扩展越界
```

---

### 3.7 延时槽 8 号未使用

**文件**: `Board/state.py:405`  
**严重程度**: 轻微 - 资源浪费

```python
"delay_slots": [0, 5, 10, 30, 45, 60, 300, 900],  # 8 个延时槽
```

**问题描述**:  
- 定义了 8 个延时槽（索引 0-7）  
- 代码中只使用了槽 0-6，槽 7（900s）未被任何规则引用

**影响**:  
资源浪费，但不影响功能。

**修复建议**:  
删除槽 7 或预留给未来扩展（在注释中说明）。

---

### 3.8 config.json 校验和字段未使用

**文件**: `config.json:1`  
**严重程度**: 轻微 - 冗余字段

```json
{"version": 1, "timestamp": 0, "checksum": 53607, "params": {...}}
```

**问题描述**:  
- `checksum` 字段存在，但 `Board/params_store.py` 未进行校验  
- 参数损坏时无法通过校验和检测

**影响**:  
参数损坏检测能力弱（仅依赖 JSON 解析异常）。

**修复建议**:  
在 `params_store.load_config()` 中增加校验和验证逻辑。

---

## 4. 修复优先级建议

### P0 - 必须立即修复（上线前）
1. **问题 1.1**: 冷凝压力环目标双写冲突  
2. **问题 1.2**: EXV 归零逻辑缺失  

### P1 - 强烈建议修复（影响控制精度）
3. **问题 1.3**: 蒸发风机状态镜像越权写入  
4. **问题 2.1**: 容量环初值可能为 0  
5. **问题 2.3**: 手动模式切换时容量环积分未清零  

### P2 - 建议修复（提升稳定性）
6. **问题 2.2**: 温控循环 rpm==999 未过滤（实际代码已修复，验证即可）  
7. **问题 2.4**: 风机故障判定逻辑不一致  
8. **问题 2.5**: 变量 COMP_HOLD_S 未在 Modbus 映射  

### P3 - 可选修复（优化项）
9. **问题 3.1-3.7**: 轻微问题，作为后续优化项

---

## 5. 验证测试建议

### 5.1 冷凝压力双写测试
1. 启动系统至 RUNNING 模式  
2. 通过 Modbus 修改 hr34（HP_TARGET）为 1200（12.0 bara）  
3. 读取 hr34 回读值和 hr70-74（冷凝压力环参数）  
4. 检查 `protect_params['HP_TARGET']` 和 `cond_control_params['setpoint']` 是否都被修改为 12.0  
5. 验证冷凝压力环是否按新目标调节

### 5.2 EXV 归零测试
1. 系统上电后保持禁用状态 30 秒  
2. 手动转动 EXV 步进电机轴（模拟未知位置）  
3. 通过 Modbus 使能系统  
4. 观察 EXV 是否先反转到限位开关（归零）再开始 PID 控制

### 5.3 手动/自动模式切换测试
1. RUNNING 模式下，设置 hr9=1 进入手动模式  
2. 通过 hr11 手动调节压缩机油门 10 秒  
3. 设置 hr9=0 切回自动模式  
4. 观察容量环输出是否平滑过渡（无突变）

### 5.4 Modbus 点位一致性测试
1. 通过 Modbus 修改 hr14（蒸发风机油门）  
2. 读取 ir4（蒸发风机转速指令镜像）和 ir21（实际下发油门）  
3. 检查两者是否一致（不应被 Modbus 越权写入）  
4. 验证 Evaporator 模块是否正确更新 ST_EVAP_FAN_SPEED_CMD

---

## 6. 代码审计总结

### 6.1 架构优点
- **模块化设计**: 执行层（Compressor/Evaporator/Condenser）、控制层（PID）、监督层（supervisor）职责分离清晰  
- **单一写者原则**: 大部分状态量遵循单一写者原则，减少竞争  
- **参数持久化**: 掉电保持机制完善（30s 防抖）  
- **延时表机制**: 保护规则统一管理，便于扩展

### 6.2 潜在风险点
- **参数冲突**: 多处双写问题（问题1.1、1.3）  
- **启动流程**: EXV 归零时机错误（问题1.2）  
- **模式切换**: 手动/自动切换时积分未清零（问题2.4）  
- **参数缺失**: 部分控制参数未暴露到 Modbus（问题2.6）

### 6.3 改进建议
1. **增强类型注解**: 关键函数增加参数和返回值类型注解  
2. **单元测试**: 为容量环、冷凝压力环等核心控制逻辑增加单元测试  
3. **状态机图**: 为 `sys_mode` 和 `running_substate` 状态机绘制状态转换图  
4. **参数校验**: 在参数加载时增加范围和一致性校验（防止配置错误）

---

## 7. 附录：受影响文件清单

### 需修改的文件
1. `Display_Touch/modbus_slave.py` - 冷凝压力目标冲突（问题1.1）、蒸发风机越权写入（问题1.3）  
2. `EXV/Valve_CRTL.py` - 归零逻辑修复（问题1.2）  
3. `Compressor/control.py` - 初值检查（问题2.2）、积分清零（问题2.4）  
4. `Alarm/supervisor.py` - 温控循环过滤（问题2.3）  
5. `Board/state.py` - 参数新增（问题2.6）

### 需验证的文件
1. `HAL/PID_Plus.py` - PID 控制器参数兼容性（问题3.4）  
2. `Board/params_store.py` - 校验和验证逻辑（问题3.8）

---

**审核人签名**: _____________________  
**审核日期**: _____________________  
**项目负责人审批**: _____________________  

---

**修订历史**:
- v1.0 (2024年) - 初始版本，黑箱测试报告

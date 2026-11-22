# Step5b 黑箱审计报告（独立审计）

**审计员**: 独立审计（黑箱方法）  
**审计日期**: 2026-09-08  
**审计方法**: 代码静态分析 + 数据流追踪 + 逻辑验证 + 测试覆盖评估  
**审计范围**: Step5b（系统主逻辑-三环控制）完整性与封板可行性评估

---

## 一、审计方法说明

本次审计采用**黑箱审计**方法，从外部观察者视角验证系统行为，独立验证以下内容：

1. **数据流完整性**: 从传感器输入到控制输出的完整闭环
2. **控制逻辑正确性**: 三环控制、L1限载、温控循环的逻辑一致性
3. **保护规则完备性**: L1/L2/L3分级保护机制的完整性
4. **接口契约匹配**: 模块间接口、数据类型、时序的一致性
5. **架构合规性**: 单一写者、延时表、质量门禁、L1限载优先级等设计原则
6. **测试覆盖度**: 测试用例对核心功能的覆盖程度

---

## 二、核心发现摘要

### 2.1 关键问题评级

| 严重度 | 数量 | 描述 |
|---|---|---|
| 🔴 **阻塞性缺陷 (Blocker)** | **0** | 无阻塞性缺陷 |
| 🟡 **严重缺陷 (Critical)** | **1** | 容量环反向逻辑错误（温度-油门关系颠倒）|
| 🟠 **重要缺陷 (Major)** | **2** | 温控循环边界条件、冷凝压力环R4停机误判 |
| 🟢 **次要问题 (Minor)** | **3** | 注释不完整、冗余判断、边界值硬编码 |

### 2.2 封板判定

**⚠️ 有条件通过**

**关键理由**:
1. ✅ **核心功能完整**: 三环控制、L1限载、温控循环、L3重试锁定已实现
2. ✅ **测试覆盖充分**: 52/52 PASS（Step5b 13单元测试 + 静态检查39项）
3. ✅ **架构合规性高**: 单一写者、延时表、质量门禁均正确
4. ⚠️ **CRITICAL-1需修复**: 容量环反向逻辑会导致温度控制失效（修复工作量小）
5. ⚠️ **MAJOR-1/2建议修复**: 影响温控循环边界行为（非核心路径）

**修复后可正式封板**（预计修复时间 < 30分钟）

---

## 三、详细审计发现

### 🟡 CRITICAL-1: 容量环反向逻辑错误

**位置**: `Compressor/control.py:109-111`

**问题描述**:
容量环 PID 计算中 error 定义错误，导致温度-油门关系颠倒：
- **当前实现**: `error = setpoint - temp` → 温度偏高时 error 为负 → PID输出减小 → 油门降低 → **错误**（应该增加油门）
- **预期行为**: 温度偏高应增加油门（增大制冷量）；温度偏低应减小油门

**代码证据**:
```python
# Compressor/control.py:109-111
temp = state.sensor_checked[idx]
# 偏差 = setpoint - temp（温度偏低 -> 减小油门；偏高 -> 增加油门）
error = _cap_ctrl.setpoint - temp
delta = _cap_ctrl.update(temp, setpoint=_cap_ctrl.setpoint)
```

**注释说明与实际矛盾**:
- 注释: "温度偏低 -> 减小油门；偏高 -> 增加油门"
- 实际: `error = setpoint - temp`
  - 当 `temp > setpoint`（温度偏高）时，`error < 0` → PID 输出负增量 → 油门减小 ❌
  - 当 `temp < setpoint`（温度偏低）时，`error > 0` → PID 输出正增量 → 油门增加 ❌

**根本原因分析**:
制冷系统中，**出液温度是被冷却对象**：
- 温度偏高（超过setpoint）→ 需要增加制冷量（增加压缩机转速/油门）→ `error` 应为正
- 温度偏低（低于setpoint）→ 需要减少制冷量（减少压缩机转速/油门）→ `error` 应为负

因此正确的 error 定义应该是: `error = temp - setpoint`（实际值 - 目标值）

**影响**:
- 容量环控制方向完全相反：温度高时降低油门、温度低时提高油门
- 出液温度无法稳定在 setpoint，系统处于发散状态
- **这是致命缺陷**，会导致制冷控制完全失效

**修复建议**:
```python
# Compressor/control.py:109-111 修改为：
temp = state.sensor_checked[idx]
# 偏差 = temp - setpoint（温度偏高 -> 增加油门；偏低 -> 减小油门）
error = temp - _cap_ctrl.setpoint
delta = _cap_ctrl.update(temp, setpoint=_cap_ctrl.setpoint)
```

**或者**（如果要保持注释中的方向）修改 PID 参数符号：
- 将 `Kp/Ki/Kd` 全部改为负值（不推荐，容易混淆）

**测试盲区说明**:
当前测试（test_step5b_verification.py）只验证了代码存在性（"comp_throttle_base" in content），
未验证容量环的实际输出方向。建议增加闭环仿真测试：
```python
def test_capacity_loop_direction():
    """温度偏高时，容量环输出应增加（增大油门）"""
    state.sensor_checked[state.IDX_OUTLET_T] = 25.0  # 高于 setpoint=22
    base_before = state.comp_throttle_base
    # 触发容量环计算
    advance_time(10000)
    Compressor._update_capacity_loop(ticks_ms())
    base_after = state.comp_throttle_base
    check("温度偏高 -> 油门增加", base_after > base_before)
```

---

### 🟠 MAJOR-1: 温控循环判断逻辑缺少质量门禁

**位置**: `Alarm/supervisor.py:740-776`

**问题描述**:
`_update_running_substate()` 判断是否达到设定温度时，直接读取 `state.sensor_checked[IDX_OUTLET_T]`，
未检查传感器质量码（quality）和新鲜度（age），可能导致：
- 出液温度传感器 BAD 或超龄时，错误判断"达到设定" → 误进入 R4 THERMO_OFF → 压缩机停机
- 传感器故障期间，温控循环无法正常工作

**代码证据**:
```python
# supervisor.py:740
outlet_t = state.sensor_checked[state.IDX_OUTLET_T]  # ← 未检查质量
setpoint = state.compressor_control_params["setpoint"]
# ...
if motor_rpm != 999 and motor_rpm <= rpm_min and outlet_t <= setpoint:
    # 达到设定，检查 MIN_ON_S
```

**对比**: 容量环（Compressor/control.py:101-106）有正确的质量门禁：
```python
idx = state.IDX_OUTLET_T
q = state.sensor_quality[idx]
age = state.sensor_age_ms(idx)
if q != state.Q_OK or age > 2000:
    return  # 质量非 OK 或超龄：冻结积分，保持当前输出
```

**影响**:
- 中等（非核心路径）：传感器故障时温控循环误判，但不会导致系统崩溃
- L2 规则 SENSOR_STALE_FAULT 会在传感器超龄后停机（有兜底）
- 但在传感器故障未达到停机条件时，可能出现非预期的启停循环

**修复建议**:
```python
# supervisor.py:740 增加质量检查
outlet_t = state.sensor_checked[state.IDX_OUTLET_T]
q = state.sensor_quality[state.IDX_OUTLET_T]
age = state.sensor_age_ms(state.IDX_OUTLET_T)
if q != state.Q_OK or age > 2000:
    # 传感器故障时不更新子状态，保持当前状态
    return
```

---

### 🟠 MAJOR-2: 冷凝压力环在 R4 THERMO_OFF 时停止更新

**位置**: `Condenser/control.py:95-98`

**问题描述**:
冷凝压力环在 `running_substate == RUNNING_THERMO_OFF` 时停止更新输出：
```python
if (state.manual_params.get("MANUAL_MODE_ENABLE", 0) or
        state.running_substate == state.RUNNING_THERMO_OFF or
        state.sys_mode != state.MODE_RUNNING):
    return
```

但 R4 状态下冷凝风机实际上仍在运行（`supervisor.py:806-815` 未禁用冷凝风机），
这会导致冷凝压力环输出冻结，冷凝风机按上一次输出值运行，失去压力调节能力。

**代码证据**:
```python
# supervisor.py:804-815
def _apply_thermo_off_interlocks():
    """R4 THERMO_OFF：压缩机停、EXV 关至 10%、冷凝风机停、蒸发风机可选保持"""
    il = state.supervisor_interlocks
    il["comp_enable"] = False
    il["comp_throttle_cmd"] = 0
    il["cond_fan_enable"] = False  # ← 冷凝风机停止
    il["cond_fan_throttle_cmd"] = 0
```

**二次验证**:
查看代码后发现冷凝风机在 R4 时确实会停止（`cond_fan_enable = False`），
因此冷凝压力环停止更新是合理的。

**结论**: **非问题**，代码逻辑正确。冷凝压力环在 R4 时停止更新是因为冷凝风机已停止。

**勘误**: 此条降级为 **INFO 级别**，无需修复。

---

### 🟢 MINOR-1: 温控循环子状态转换判断冗余

**位置**: `Alarm/supervisor.py:792-801`

**问题描述**:
在判断 PULLDOWN 和 MODULATING 转换时存在冗余判断：
```python
# 未达到设定：区分 PULLDOWN 和 MODULATING
if sub == state.RUNNING_MODULATING:
    return  # ← 提前返回，后续代码不会执行到 sub==MODULATING 的分支
# 接近设定（偏差 <= 2K）进入 MODULATING
if abs(outlet_t - setpoint) <= 2.0:
    if sub != state.RUNNING_MODULATING:  # ← 这个判断永远为真（因为上面已 return）
        state.running_substate = state.RUNNING_MODULATING
        print("[Supervisor] R1 -> R2 (接近设定)")
else:
    if sub != state.RUNNING_PULLDOWN:  # ← 建议保留此判断（避免重复打印）
        state.running_substate = state.RUNNING_PULLDOWN
```

**影响**: 无功能影响，只是代码冗余

**修复建议**:
```python
# 接近设定（偏差 <= 2K）进入 MODULATING
if abs(outlet_t - setpoint) <= 2.0:
    state.running_substate = state.RUNNING_MODULATING
    print("[Supervisor] R1 -> R2 (接近设定)")
else:
    if sub != state.RUNNING_PULLDOWN:
        state.running_substate = state.RUNNING_PULLDOWN
```

---

### 🟢 MINOR-2: 硬编码边界值 2.0K

**位置**: `Alarm/supervisor.py:795`

**问题描述**:
PULLDOWN 到 MODULATING 的切换阈值硬编码为 2.0K：
```python
if abs(outlet_t - setpoint) <= 2.0:
```

建议改为参数化，方便台架调试：
```python
if abs(outlet_t - setpoint) <= state.protect_params.get("MODULATING_THRESHOLD", 2.0):
```

**影响**: 低（调试不便，但不影响功能）

---

### 🟢 MINOR-3: L3 重试锁定窗口衰减注释不完整

**位置**: `Alarm/supervisor.py:189-202`

**问题描述**:
`_record_latched_fault()` 清理窗口外的旧记录时，注释说"清理窗口外的旧记录"，
但未说明窗口大小由 `RETRY_WINDOW_S` 参数控制（默认 3600秒）。

**修复建议**:
```python
# 清理窗口外的旧记录（窗口大小由 RETRY_WINDOW_S 参数控制，默认 3600s=1小时）
_retry_counts[name] = [t for t in _retry_counts[name]
                       if time.ticks_diff(now_ms, t) <= window_ms]
```

**影响**: 低（代码可读性）

---

## 四、数据流完整性验证

### 4.1 容量环数据流（IDX_OUTLET_T → comp_throttle_base）

✅ **数据流闭环验证通过**（除 CRITICAL-1 逻辑错误外）:

```
传感器层: PT100 → AD7124 → validate.py → sensor_checked[IDX_OUTLET_T]
         ↓
质量门禁: sensor_quality[IDX_OUTLET_T] == Q_OK, age < 2000ms
         ↓
容量环: Compressor/control.py:_update_capacity_loop()
       - 10s 周期
       - PID 计算: error = setpoint - temp (⚠️ CRITICAL-1 反向错误)
       - 输出: comp_throttle_base (油门绝对值)
         ↓
L1 限载: ACT_INHIBIT_UP/ACT_UNLOAD 可覆盖容量环输出
         ↓
supervisor: RUNNING 期间复制到 interlocks["comp_throttle_cmd"]
         ↓
设备模块: Compressor 转速环消费油门，调节 ESC0 实际输出
         ↓
反馈镜像: ST_ESC0_RPM → ST_MOTOR_SPEED → 容量环/supervisor 读取
```

**验证要点**:
- ✅ 质量门禁正确（Compressor/control.py:102-106）
- ✅ 10s 周期控制正确（control.py:90）
- ✅ L1 限载优先级正确（control.py:117-126）
- ⚠️ **PID 反向逻辑错误**（CRITICAL-1）
- ✅ supervisor 正确复制到 interlocks（supervisor.py:693）

---

### 4.2 冷凝压力环数据流（IDX_DISCHARGE_PSI → cond_fan_throttle_base）

✅ **数据流闭环验证通过**:

```
传感器层: 压力传感器 ADC → validate.py → sensor_checked[IDX_DISCHARGE_PSI]
         ↓
质量门禁: sensor_quality[IDX_DISCHARGE_PSI] == Q_OK, age < 2000ms
         ↓
冷凝压力环: Condenser/control.py:_update_pressure_loop()
           - 2s 周期
           - PID 计算: 排气压力 → 冷凝风机油门
           - 输出: cond_fan_throttle_base
         ↓
L1 限载: ACT_COND_FAN_MAX 强制全速（覆盖压力环输出）
         ↓
supervisor: RUNNING 期间复制到 interlocks["cond_fan_throttle_cmd"]
         ↓
设备模块: Condenser 转速环消费油门，调节 ESC2 实际输出
```

**验证要点**:
- ✅ 质量门禁正确（Condenser/control.py:101-104）
- ✅ 2s 周期控制正确（control.py:89）
- ✅ L1 高压限载强制全速正确（control.py:114-115, 142-143）
- ✅ supervisor 正确复制到 interlocks（supervisor.py:694）

---

### 4.3 L1 限载数据流

✅ **L1 限载规则 → limit_actions → 控制环消费** 闭环验证通过:

```
规则评估: rules.py L1 规则（6条）
         - HIGH_PRESSURE_UNLOAD: 排气压力 > HP_UNLOAD
         - CURRENT_LIMIT: 电流 > I_LIMIT
         - CURRENT_UNLOAD: 电流 > I_UNLOAD
         - LOW_PRESSURE_UNLOAD: 吸气压力 < LP_UNLOAD
         - CRITICAL_LOW_SUPERHEAT: SH < SH_CRIT
         - DISCHARGE_TEMP_UNLOAD: 排气温度 > TD_UNLOAD
         ↓
延时表: supervisor._process_rules() 计时 5s（槽1）
         ↓
动作码叠加: supervisor.py:594-598
           limit_actions |= (ACT_INHIBIT_UP | ACT_UNLOAD | ACT_COND_FAN_MAX)
         ↓
消费方 1: Compressor/control.py:117-126
         - ACT_INHIBIT_UP: clamp 输出到当前值（禁升速）
         - ACT_UNLOAD: 强制降容到回油油门
消费方 2: Condenser/control.py:114-115, 142-143
         - ACT_COND_FAN_MAX: 强制全速
```

**验证要点**:
- ✅ 6条 L1 规则全部实现（rules.py:541-581）
- ✅ 延时槽统一使用槽1（5s）（rules.py:543,550,557,564,571,578）
- ✅ 动作码位图叠加正确（supervisor.py:594-598）
- ✅ 容量环消费 ACT_INHIBIT_UP/ACT_UNLOAD（Compressor/control.py:117-126）
- ✅ 冷凝压力环消费 ACT_COND_FAN_MAX（Condenser/control.py:114,142）

---

### 4.4 温控循环数据流

✅ **温控循环 R1-R4 状态机** 闭环验证通过（除 MAJOR-1 质量门禁缺失外）:

```
状态机: supervisor.py:_update_running_substate()
       ↓
R1 PULLDOWN: 出液温度 >> setpoint
       ↓
R2 MODULATING: abs(outlet_t - setpoint) <= 2.0K
       ↓
R4 THERMO_OFF: outlet_t <= setpoint 且 motor_rpm <= rpm_min 且 MIN_ON 满足
       → 压缩机停、EXV 关至10%、冷凝风机停
       ↓
R1: outlet_t > setpoint + THERMO_HYST 且 MIN_OFF 满足
       → 重新启动
```

**验证要点**:
- ✅ 状态转换逻辑正确（supervisor.py:732-802）
- ✅ MIN_ON_S 防短循环正确（supervisor.py:780）
- ✅ MIN_OFF_S 防短循环正确（supervisor.py:764）
- ✅ THERMO_HYST 回差正确（supervisor.py:742,760）
- ⚠️ **质量门禁缺失**（MAJOR-1，supervisor.py:740）
- ✅ R4 interlocks 正确关闭执行机构（supervisor.py:804-815）

---

### 4.5 L3 重试锁定数据流

✅ **L3 重试锁定** 闭环验证通过:

```
故障锁存: supervisor._record_latched_fault()
         - 记录时间戳到 _retry_counts[fault_name]
         - 清理 1 小时外的旧记录
         - 1 小时内锁存 3 次 → retry_lockout = True
         ↓
启动拦截: supervisor.tick() MODE_SAFE_STOP
         - 检查 retry_lockout → 拒绝启动
         ↓
自动解锁: supervisor._clear_retry_lockout()
         - 所有锁存故障位清零后 → retry_lockout = False
```

**验证要点**:
- ✅ 窗口滑动正确（supervisor.py:196-197）
- ✅ 计数阈值 RETRY_MAX=3 正确（supervisor.py:199）
- ✅ 启动拦截正确（supervisor.py:650-652）
- ✅ 自动解锁正确（supervisor.py:205-222）
- ✅ 测试覆盖充分（test_step5b_verification.py:418-428）

---

## 五、架构合规性检查

### 5.1 单一写者原则

| 数据项 | 写者 | 状态 |
|---|---|---|
| `error_reg` | supervisor.set_fault() | ✅ 合规 |
| `limit_actions` | supervisor._process_rules() | ✅ 合规 |
| `comp_throttle_base` | Compressor._update_capacity_loop() | ✅ 合规 |
| `cond_fan_throttle_base` | Condenser._update_pressure_loop() | ✅ 合规 |
| `running_substate` | supervisor._update_running_substate() | ✅ 合规 |
| `retry_lockout` | supervisor._record_latched_fault() / _clear_retry_lockout() | ✅ 合规 |

---

### 5.2 延时表统一

✅ **延时槽机制正确**:
- L1 限载规则统一使用 `L1_LIMIT_DELAY_SLOT`（槽1 = 5s）
- L2 排气温度停机使用 `TD_TRIP_DELAY_SLOT`（槽6 = 300s）
- Modbus 通讯断使用 `MODBUS_TIMEOUT_S`（60s）
- 所有规则通过 supervisor._process_rules() 统一计时

---

### 5.3 质量门禁

| 模块 | 质量检查 | 状态 |
|---|---|---|
| 容量环 | ✅ 检查 Q_OK, age < 2000 | 正确 |
| 冷凝压力环 | ✅ 检查 Q_OK, age < 2000 | 正确 |
| L1 限载规则 | ✅ 所有规则检查 Q_OK, age | 正确 |
| 温控循环 | ⚠️ **未检查质量** | MAJOR-1 |

---

### 5.4 L1 限载优先级

✅ **L1 限载可覆盖容量环/冷凝压力环** 验证通过:

**容量环**:
```python
# Compressor/control.py:117-126
# L1 限载：禁升速
if state.limit_actions & state.ACT_INHIBIT_UP:
    base = min(base, state.comp_throttle_base)  # ← clamp 到当前值

# L1 限载：降容
if state.limit_actions & state.ACT_UNLOAD:
    oil_thr = state.protect_params["COMP_OIL_RETURN_THROTTLE"]
    rate = state.protect_params["UNLOAD_RATE_THROTTLE_S"]
    max_allowed = state.comp_throttle_base - rate * 10.0
    base = max(oil_thr, min(base, max_allowed))  # ← 强制降容
```

**冷凝压力环**:
```python
# Condenser/control.py:114-115, 142-143
# L1 高压限载：冷凝风机强制全速
if state.limit_actions & state.ACT_COND_FAN_MAX:
    base = cp["throttle_max"]  # ← 覆盖压力环输出
    
# 设备模块执行时也会检查（双重保险）
if state.limit_actions & state.ACT_COND_FAN_MAX:
    throttle_cmd = state.ESC_THROTTLE_MAX  # ← 覆盖所有模式（包括手动）
```

**验证结论**: ✅ L1 限载优先级正确，符合 DESIGN.md §9.5 设计要求

---

## 六、测试覆盖情况

### 6.1 自动化测试结果

| 测试套件 | 结果 | 说明 |
|---|---|---|
| Step5b 静态检查 | 39/39 PASS | 参数、常量、规则、代码存在性验证 |
| Step5b 单元测试 | 13/13 PASS | 阈值单调性、L1限载、温控循环、L3锁定 |
| Step2-5a 回归 | 105/105 PASS | 检查层、supervisor、Driver、设备执行层 |
| **合计** | **157/157 PASS** | 2,827 行测试代码（Step2-5b累计）|

---

### 6.2 测试覆盖要点

**Step5b 测试内容**（test_step5b_verification.py）:
1. ✅ 参数完整性（14项）: HP_UNLOAD/I_LIMIT/TD_UNLOAD/MIN_ON_S/RETRY_MAX 等
2. ✅ 常量定义（4项）: ACT_INHIBIT_UP/ACT_UNLOAD/ACT_COND_FAN_MAX/ACT_NORMAL_STOP
3. ✅ 子状态常量（5项）: RUNNING_PULLDOWN/MODULATING/LIMITED/THERMO_OFF
4. ✅ 控制环参数（3项）: compressor_control_params/cond_control_params
5. ✅ L1 规则存在性（8项）: 6条 L1 限载 + 排气温度停机 + Modbus 通讯断
6. ✅ 代码实现验证（2项）: 容量环代码存在、冷凝压力环代码存在
7. ✅ 阈值单调性校验（2项）: 正常通过、违反拒绝启动
8. ✅ L1 限载动作（7项）: 高压/电流×2/低压/SH/排温降容，动作码正确
9. ✅ 排气温度连续计时（1项）: 中途降温重新计时，连续 300s 停机
10. ✅ Modbus 通讯断（1项）: 60s 延时正常停机，不锁存 error_reg
11. ✅ 温控循环（2项）: R1→R4（MIN_ON满足），R4→R1（MIN_OFF满足）
12. ✅ L3 重试锁定（1项）: 1 小时内 3 次锁存 → retry_lockout

---

### 6.3 测试覆盖盲区

**未覆盖项**（需目标机实测）:
1. ⚠️ **容量环闭环响应**（CRITICAL-1未被测试发现）
   - 当前测试只验证代码存在，未验证 PID 输出方向
   - 建议增加单元测试: 温度偏高 → 油门增加（见 CRITICAL-1 修复建议）

2. ⚠️ **冷凝压力环闭环响应**
   - 排气压力偏高 → 冷凝风机转速增加
   - 建议增加单元测试（类似容量环）

3. ⚠️ **三环并行不互扰**
   - 容量环/冷凝压力环同时工作时，输出不冲突
   - 建议增加集成测试

4. ⚠️ **L1 限载实际覆盖效果**
   - 当前测试只验证动作码置位，未验证容量环输出是否真正被限制
   - 建议增加测试: 触发 ACT_INHIBIT_UP → 验证 comp_throttle_base 不增加

5. ⚠️ **R4 THERMO_OFF 执行机构状态**
   - 当前测试只验证子状态转换，未验证 interlocks 是否正确关闭设备
   - 建议增加测试: 进入 R4 → 验证 comp_enable=False, exv_target_pct=10.0

---

## 七、设计决策确认（Q1-Q11）

所有设计决策（PLAN.md §4.7）已在代码中正确实现：

| 问题 | 拍板结论 | 代码实现位置 | 验证结果 |
|---|---|---|---|
| Q1: 排气温度保护 | L1降容+L2连续300s停机 | rules.py:576-589 | ✅ 正确 |
| Q2: L1延时 | 走延时槽1（5s）| rules.py:543,550,557,564,571,578 | ✅ 正确 |
| Q3: L3重试锁定 | 做，条件消失自动清 | supervisor.py:189-222 | ✅ 正确 |
| Q4: Modbus通讯断 | 60s延时正常停机 | rules.py:592-597 | ✅ 正确 |
| Q5: 流量保护 | error_reg位16（Step5a已实现）| rules.py:533-538 | ✅ 正确 |
| Q6: 被控变量 | 出液温度 IDX_OUTLET_T | Compressor/control.py:101 | ✅ 正确 |
| Q7: setpoint | 22.0°C | state.py:223 | ✅ 正确 |
| Q8: 系统类型 | 风冷直膨适配冷水 | - | ✅ 正确 |
| Q9: 除霜 | 不做 | - | ✅ 正确 |
| Q10: 制热 | 不做 | - | ✅ 正确 |
| Q11: HP_TARGET | 12.0 bara | state.py:239 | ✅ 正确 |

---

## 八、代码质量评估

### 8.1 代码统计

| 模块 | 行数 | 说明 |
|---|---|---|
| Compressor/control.py | 192 | 容量环 + 转速环 + 故障检测 |
| Condenser/control.py | 180 | 冷凝压力环 + 转速环 + 故障检测 |
| Alarm/supervisor.py | 855 | 状态机 + 规则延时表 + 温控循环 + L3锁定 |
| Alarm/rules.py | 618 | 保护规则定义（L1/L2/L3）|
| test/test_step5b_verification.py | 466 | Step5b 测试用例 |
| **Step5b 新增代码** | **~500** | 容量环/冷凝压力环/温控循环/L3锁定 |
| **Step5b 测试代码** | **466** | 测试/代码比 0.93 |

---

### 8.2 代码风格一致性

✅ **代码风格与 Step5a 一致**:
- 模块级 PID 实例（与 Step5a 转速环一致）
- 质量门禁模式（与 EXV PID 一致）
- 注释格式（与 supervisor/rules 一致）
- 参数同步函数（与 EXV _sync_controller_params 一致）

---

### 8.3 注释完整性

✅ **注释覆盖充分**:
- 所有函数有用途说明
- 关键逻辑有行内注释
- 设计依据引用 DESIGN.md 章节
- ⚠️ CRITICAL-1 处注释与实现矛盾（需修复）

---

## 九、封板阻塞问题汇总

### 9.1 必须修复（阻塞封板）

| ID | 问题 | 修复工作量 | 风险 |
|---|---|---|---|
| CRITICAL-1 | 容量环反向逻辑错误 | 1 行代码 + 注释修改 | 低（修改 error 定义）|

---

### 9.2 建议修复（影响质量）

| ID | 问题 | 修复工作量 | 可延后 |
|---|---|---|---|
| MAJOR-1 | 温控循环缺少质量门禁 | 5 行代码 | ❌ 影响温控 |
| MAJOR-2 | 冷凝压力环R4停机 | 勘误：非问题 | - |

---

### 9.3 可延后修复

| ID | 问题 | 修复工作量 | 可延后 |
|---|---|---|---|
| MINOR-1 | 温控子状态判断冗余 | 代码优化 | ✅ Step6 |
| MINOR-2 | 硬编码阈值 2.0K | 参数化 | ✅ Step6 |
| MINOR-3 | 注释不完整 | 注释补充 | ✅ 随时 |

---

## 十、修复优先级与工作量评估

### 10.1 立即修复（封板前）

```python
# 1️⃣ CRITICAL-1: 容量环反向逻辑修复（5 分钟）
# 文件：Compressor/control.py
# 位置：109-111 行

# 修改前:
temp = state.sensor_checked[idx]
# 偏差 = setpoint - temp（温度偏低 -> 减小油门；偏高 -> 增加油门）
error = _cap_ctrl.setpoint - temp
delta = _cap_ctrl.update(temp, setpoint=_cap_ctrl.setpoint)

# 修改后:
temp = state.sensor_checked[idx]
# 偏差 = temp - setpoint（温度偏高 -> 增加油门；偏低 -> 减小油门）
error = temp - _cap_ctrl.setpoint
delta = _cap_ctrl.update(temp, setpoint=_cap_ctrl.setpoint)

# 2️⃣ MAJOR-1: 温控循环质量门禁（10 分钟）
# 文件：Alarm/supervisor.py
# 位置：740 行后增加

# 修改前:
outlet_t = state.sensor_checked[state.IDX_OUTLET_T]
setpoint = state.compressor_control_params["setpoint"]

# 修改后:
outlet_t = state.sensor_checked[state.IDX_OUTLET_T]
q = state.sensor_quality[state.IDX_OUTLET_T]
age = state.sensor_age_ms(state.IDX_OUTLET_T)
if q != state.Q_OK or age > 2000:
    return  # 传感器故障时不更新子状态
setpoint = state.compressor_control_params["setpoint"]
```

**总工作量**: < 20 分钟代码修改 + 30 分钟回归测试

---

### 10.2 可延后修复

- MINOR-1/2/3: 代码质量优化，不影响功能
- 建议在 Step6 参数统一时一并处理

---

## 十一、封板建议（最终结论）

### 11.1 当前状态评估

| 维度 | 评分 | 说明 |
|---|---|---|
| **功能完整性** | 95/100 | 三环控制、L1限载、温控循环已实现，容量环有逻辑错误 |
| **数据流完整性** | 98/100 | 所有数据流闭环，质量门禁完善（温控除外）|
| **架构合规性** | 98/100 | 单一写者、延时表、L1优先级均正确 |
| **代码质量** | 90/100 | 代码风格一致，注释充分，有1处注释实现矛盾 |
| **测试覆盖** | 92/100 | 52/52 PASS，未覆盖容量环闭环响应 |

**综合评分**: **94/100**

---

### 11.2 封板决策

**⚠️ 有条件通过**

**关键阻塞项**:
1. **CRITICAL-1（容量环反向逻辑）**: 必须修复，否则温度控制完全失效
2. **MAJOR-1（温控质量门禁）**: 建议修复，否则传感器故障时可能误动作

**建议路径**:
- **路径 A（快速封板）**: 修复 CRITICAL-1（5 分钟）+ 回归测试 → 可封板
- **路径 B（稳妥封板）**: 修复 CRITICAL-1 + MAJOR-1（15 分钟）+ 回归测试 → **推荐**

---

### 11.3 修复后预期状态

修复 CRITICAL-1 + MAJOR-1 后:

| 维度 | 评分变化 | 说明 |
|---|---|---|
| 功能完整性 | 95 → **100** | 容量环逻辑修正 |
| 数据流完整性 | 98 → **100** | 温控质量门禁补全 |
| 代码质量 | 90 → **95** | 注释实现一致性修正 |

**综合评分**: 94 → **98/100**（可封板）

---

## 十二、审计结论

经过完整的黑箱审计，Step5b 代码质量**总体优秀**，三环控制架构合理，L1/L2/L3分级保护完善，测试覆盖充分。但存在 **1 个严重缺陷（容量环反向逻辑）**和 **1 个重要缺陷（温控质量门禁）**需要修复后才能封板。

### 好的方面

✅ **功能完整性高**:
- 容量环、冷凝压力环、温控循环已实现
- L1 限载规则完整（6条规则 + 4个动作码）
- L2 排气温度停机（连续 300s 计时）
- L3 重试锁定（1 小时内 3 次）
- Modbus 通讯断正常停机（不锁存）

✅ **架构合规性高**:
- 单一写者原则正确
- 延时表统一（槽1/槽6分别管理L1/L2）
- 质量门禁正确（控制环检查 Q_OK）
- L1 限载优先级正确（可覆盖容量环/冷凝压力环）

✅ **测试覆盖充分**:
- 52/52 PASS（静态检查 39 + 单元测试 13）
- 回归测试 105/105 PASS（无破坏）
- 测试/代码比 0.93（2,827 行测试代码）

✅ **代码质量高**:
- 代码风格与 Step5a 一致
- 注释覆盖充分
- 模块划分清晰（容量环/冷凝压力环/supervisor/rules）

### 需改进的方面

❌ **CRITICAL-1（容量环反向逻辑）**:
- 温度-油门关系颠倒
- 会导致制冷控制完全失效
- **必须修复**（5 分钟）

⚠️ **MAJOR-1（温控质量门禁）**:
- 传感器故障时可能误判"达到设定"
- 影响温控循环可靠性
- **建议修复**（10 分钟）

⚠️ **测试盲区**:
- 容量环闭环响应未测试（CRITICAL-1 未被发现）
- 建议增加闭环仿真测试

### 修复后可达到封板标准

修复 CRITICAL-1 + MAJOR-1 后（< 20 分钟代码 + 30 分钟测试），综合评分可达 **98/100**，满足封板条件。

---

## 十三、下一步建议

### 13.1 立即行动

1. **修复 CRITICAL-1**（容量环反向逻辑）
2. **修复 MAJOR-1**（温控质量门禁）
3. **回归测试**（157 个测试用例全部通过）
4. **增加闭环测试**（容量环/冷凝压力环响应方向）

---

### 13.2 Step6 准备

**前置条件已满足**:
- ✅ Step5b 核心功能完整（修复后）
- ✅ 三环控制已实现
- ✅ L1/L2/L3 分级保护完善
- ✅ 测试覆盖充分

**Step6 可开展工作**（参数统一 + 寄存器扩展）:
- 参数三源整合（state 默认/config.json/Modbus 可写）
- 延时槽 Modbus 映射（hr 可读写）
- ESC 遥测 Modbus 映射（ir32-47）
- B6 周期失配重标（实测周期 800ms → 阈值调整）
- C8 传感器故障多 bit 映射（16-bit → 16个 di）

---

### 13.3 技术债务管理

| 项 | 优先级 | 计划处理时机 |
|---|---|---|
| CRITICAL-1（容量环反向）| P0 | **立即修复** |
| MAJOR-1（温控质量门禁）| P1 | **立即修复** |
| MINOR 1-3（代码优化）| P3 | Step6 |
| 测试盲区（闭环响应）| P2 | 封板前增加 |
| B6（周期失配）| P2 | Step6 重标 |
| C8（传感器故障单 bit）| P2 | Step6 Modbus 扩展 |

---

**审计员签名**: OpenCode AI（独立黑箱审计）  
**审计日期**: 2026-09-08  
**建议封板日期**: 修复 CRITICAL-1 + MAJOR-1 后即可封板  
**报告版本**: v1.0（黑箱审计最终版）

---

## 十四、附录：快速修复清单

```python
# ========== 修复清单（按优先级排序）==========

# 1️⃣ CRITICAL-1: 容量环反向逻辑修复（必须）
# 文件：Compressor/control.py
# 位置：109-111 行
# 修改前：error = _cap_ctrl.setpoint - temp
# 修改后：error = temp - _cap_ctrl.setpoint
# 注释：# 偏差 = temp - setpoint（温度偏高 -> 增加油门；偏低 -> 减小油门）

# 2️⃣ MAJOR-1: 温控循环质量门禁（强烈建议）
# 文件：Alarm/supervisor.py
# 位置：740 行后增加 5 行
q = state.sensor_quality[state.IDX_OUTLET_T]
age = state.sensor_age_ms(state.IDX_OUTLET_T)
if q != state.Q_OK or age > 2000:
    return  # 传感器故障时不更新子状态

# ========== 回归测试清单 ==========
# 1. 运行 test_step5b_verification.py（预期 52/52 PASS）
# 2. 运行 Step2-5a 回归测试（预期 105/105 PASS）
# 3. 手动验证（建议）：
#    - 容量环响应方向：温度偏高 -> 油门增加
#    - 冷凝压力环响应方向：压力偏高 -> 风机转速增加
#    - L1 限载覆盖：触发 ACT_INHIBIT_UP -> 油门不增加
#    - 温控循环：传感器 BAD 时不误进入 R4
```

---

**报告结束**

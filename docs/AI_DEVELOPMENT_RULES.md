# AI 开发规则

**用途**：AI 修改本项目时必须遵守的规则，避免破坏架构、越界修改或引入不兼容代码。

---

## 1. 修改前必读

### 1.1 强制阅读文档（按顺序）

1. **本文档**（AI_DEVELOPMENT_RULES.md）- 了解规则
2. **STRUCTURE.md** - 目录结构、模块职责、数据面定义
3. **PLAN.md** - 当前 Step、DoD、未解决问题台账
4. **DESIGN.md 对应章节** - 统一设计规范（§3 数据接口 / §5 状态机 / §6 保护策略 / §7 控制逻辑 / §10 Modbus / §11 决策记录）

### 1.2 检查现有代码

修改前必须：
- ✅ 用 `read` 工具实际查看要修改的文件
- ✅ 用 `grep` 查找调用关系
- ✅ 检查 `Board/state.py` 是否已定义相关变量
- ❌ 不根据文件名猜测功能
- ❌ 不假设"应该有"的代码

---

## 2. Step 边界规则

### 2.1 明确当前 Step

**在 `PLAN.md §1`（当前状态速览）或用户指令中确认当前 Step**

修改前回答：
1. 当前在哪个 Step？
2. 这个 Step 的 DoD（完成定义）是什么？
3. 我的修改是否在 Step 范围内？

### 2.2 禁止跨 Step 修改

| 行为 | 允许 | 说明 |
|------|------|------|
| 修改当前 Step 的代码 | ✅ | 按 DoD 要求 |
| 修改已完成 Step 的 P0 阻塞问题 | ⚠️ | 必须先报告，不擅自修改 |
| 修改未来 Step 的代码 | ❌ | 绝对禁止 |
| "顺便优化"其他模块 | ❌ | 绝对禁止 |
| 重构已完成 Step | ❌ | 绝对禁止 |

**示例**：
- ✅ Step3 修改 `Alarm/` 目录（当前 Step）
- ⚠️ Step3 发现 Step2 `validate()` 阻塞问题 → **先报告，不自行修改**
- ❌ Step3 修改 `HAL/Driver.py`（属 Step4）
- ❌ Step3 "顺便"优化 `HAL/PID_Plus.py`（不在范围）

### 2.3 范围不确定时

**行动准则**：宁可漏报，不擅自扩大

- ✅ 向用户询问："这个修改是否在 Step 范围内？"
- ✅ 列出修改影响的所有文件，请求确认
- ❌ 自行判断"应该一起改"
- ❌ "先改了再说"

---

## 3. 架构约束（铁律）

### 3.1 不得违反的架构原则

| 原则 | 说明 | 依据 |
|------|------|------|
| **显示与控制分离** | 显示读 `sensor_data[]`（999 透传），控制读 `sensor_checked[]` + 质量码 | DESIGN.md §4.1 |
| **Modbus 地址契约稳定** | 按 `MODBUS_POINT_TABLE.csv` / `MODBUS_POINT_TABLE.md` 的标准四区地址实施；变更既有点位须先更新权威点表并确认兼容性 | DESIGN.md §10 |
| **压力全程绝压** | bara 单位，不得再当表压 | DESIGN.md §2.2、§3.1 |
| **全部报警必须延时** | 无零延时跳闸；按规则表和延时槽执行 | DESIGN.md §4.4、§6 |
| **validate 只判定不动作** | 不写 error_reg，不执行 SAFE_STOP | DESIGN.md §4.1 |
| **目录不重构** | 设备逻辑放设备目录，保护逻辑放 Alarm | STRUCTURE.md §1-2 |
| **不重写 PID 算法** | HAL/PID_Plus.py 已有系统性注释 | DESIGN.md §7；代码注释 |

### 3.2 MicroPython 兼容性

| 限制 | 说明 | 替代方案 |
|------|------|----------|
| **时间 API** | 使用 `time.ticks_ms()` / `time.ticks_diff()` | ❌ 不用 `time.time()` / `datetime` |
| **json 参数** | 不支持 `encoding=` / `indent=` / `ensure_ascii=` | Step6 修复 save/load |
| **标准库限制** | 部分 CPython 库不可用 | 查阅 MicroPython 文档 |
| **固件冻结模块** | `adc_direct` / `uasyncio` 已内置 | ❌ 不可修改 |
| **内存限制** | 嵌入式环境，避免大对象 | 流式处理 |

### 3.3 并发模型

| 规则 | 说明 |
|------|------|
| **单线程协作式** | 只用 `uasyncio`，不用 `_thread` |
| **共享数据无锁** | `Board/state.py` 模块级变量，协程内安全 |
| **validate 同步执行** | 在 `reads()` 的 `finally` 内调用，不创建新协程 |
| **单一写者** | `error_reg` 只由 supervisor 写，sensor_fault_reg 只由 reads 写 |

---

## 4. 代码修改规则

### 4.1 修改 state.py 的寄存器变量

**错误示例**（UnboundLocalError）：
```python
def set_sensor_fault(name):
    sensor_fault_reg &= ~(1 << bit)  # Bug: 缺 global
```

**正确示例**：
```python
def set_sensor_fault(name):
    global sensor_fault_reg  # 必须声明
    sensor_fault_reg &= ~(1 << bit)
```

**规则**：
- ✅ 读取变量：不需要 `global`
- ✅ 修改变量（`=` / `&=` / `|=` 等）：**必须** `global`

### 4.2 修改 validate.py

**强制要求**：
- ✅ 只更新 `sensor_checked` / `sensor_quality` / `sensor_checked_ms`
- ❌ 不写 `error_reg`（属 supervisor）
- ❌ 不调用 `set_fault()`（属 supervisor）
- ❌ 不执行 SAFE_STOP（属 supervisor）
- ❌ 不创建新协程

### 4.3 修改 reads()

**强制要求**：
- ✅ 保持 `try...finally` 结构
- ✅ `finally` 内必须调用 `update_all_sensor_faults()` + `validate()`
- ✅ 单通道异常用 `try...except` 包裹，只影响该通道
- ❌ 不让异常逸出 `finally` 之前

**示例**：
```python
def reads():
    try:
        # 采样可能抛异常
        state.sensor_data[0] = sample_channel_0()
        
        # 单通道保护
        try:
            state.sensor_data[7] = calculate_sh()
        except Exception as e:
            print("[Sensor] SH 计算异常:", e)
            state.sensor_data[7] = 999  # 单通道降级
    
    finally:
        # 无论如何都执行
        state.update_all_sensor_faults()
        validate()
```

### 4.4 修改 PID 控制环

**强制要求**（Step2+ 质量门禁）：
```python
# EXV PID 示例
sh_q = state.sensor_quality[state.IDX_SUPERHEAT]
sh_age = state.sensor_age_ms(state.IDX_SUPERHEAT)

if sh_q != state.Q_OK or sh_age > 1000:
    continue  # 保持开度，跳过 update

sh = state.sensor_checked[state.IDX_SUPERHEAT]  # 只有门禁通过才读 checked
delta = pid.update(sh)
```

**规则**：
- ✅ 先查 `sensor_quality` 和 `sensor_age_ms()`
- ✅ 只有 `Q_OK` 且年龄 <= 阈值才读 `sensor_checked`
- ❌ 不直接读 `sensor_data`（显示通道专用）

### 4.5 修改 main.py

**允许**：
- ✅ 添加/删除协程到 `asyncio.gather()`
- ✅ 修改 PID 初始化参数

**禁止**：
- ❌ 在 main 内编写业务逻辑（超过 10 行）
- ❌ 在 main 内处理传感器数据
- ❌ 在 main 内写控制算法

---

## 5. 测试要求

### 5.1 PC 测试环境

**与 MicroPython 的区别**：
- MicroPython
- 需要 mock `pyb` / `machine` / `uasyncio` 模块
- 需要 mock `time.ticks_ms` / `time.ticks_diff`

**测试文件位置**：
- ✅ 放在 `test/` 目录
- ✅ 文件名 `test_stepX_*.py`
- ❌ 不放在生产代码目录

**mock 示例**（见 test/test_step2_verification.py）：
```python
import sys
import time as _time

# Mock MicroPython time
def ticks_ms():
    return int(_time.time() * 1000)

def ticks_diff(a, b):
    return a - b

class MockTime:
    ticks_ms = staticmethod(ticks_ms)
    ticks_diff = staticmethod(ticks_diff)

sys.modules['time'] = MockTime()
sys.modules['pyb'] = ...  # Mock Pin/ADC/UART
```

### 5.2 测试覆盖要求

**每个 Step 的测试必须覆盖**：
1. DoD 列出的全部验收点
2. 已修复问题的回归测试
3. 异常路径（finally 块、单通道降级）
4. 边界条件（质量码转换、超时、溢出）

**测试结果格式**：
```
[PASS] 测试项名称
[FAIL] 测试项名称
       失败详情

最终: X passed, Y failed
```

### 5.3 测试文件清理

**规则**：
- ✅ 测试完成后保留在 `test/` 目录
- ✅ 提交时说明测试文件位置
- ❌ 不删除测试文件（供后续回归）

---

## 6. 报告与确认

### 6.1 不确定时必须报告

**场景**：
- ❓ 代码与文档描述不一致
- ❓ 发现已完成 Step 的 P0 问题
- ❓ 修改范围不清楚
- ❓ 需要跨 Step 修改才能完成任务
- ❓ 发现 MicroPython 兼容性问题

**报告格式**：
```
[不确定] 问题描述

现象：...
影响：...
是否需要修改：...
建议方案：...

请确认是否允许修改。
```

### 6.2 修改前确认清单

**提交修改前自查**：
- [ ] 我已阅读 `PLAN.md §2` 当前 Step 的 DoD
- [ ] 我已确认修改在 Step 范围内
- [ ] 我已用 `read` 实际查看了要修改的文件
- [ ] 我没有修改其他 Step 的代码
- [ ] 我没有违反架构约束
- [ ] 我已添加 `global` 声明（如果修改 state 寄存器）
- [ ] 我已编写测试并通过
- [ ] 我已检查 MicroPython 兼容性

### 6.3 完成后报告格式

```
## 修改文件

1. path/to/file1.py - 修改原因
2. path/to/file2.py - 修改原因

## 问题修复

### P0-1: 问题名称
- 问题：...
- 修复：...
- 位置：file.py 第 X-Y 行

## 测试结果

- 测试文件：test/test_stepX.py
- 覆盖项：...
- 结果：X passed, Y failed

## DoD 自检

- [ ] DoD 项 1
- [ ] DoD 项 2
...

## 未修改项目

- 项目 A：不在 Step 范围（属 StepX）
- 项目 B：需要确认是否修改
```

---

## 7. 常见错误与避免

### 7.1 错误：擅自扩大修改范围

**场景**：Step3 实现 Alarm，"顺便"重构了 validate.py

**正确做法**：
- Step3 只修改 Alarm/ 和 supervisor 相关代码
- 如果发现 validate.py 有问题，**先报告，不自行修改**
- 由用户决定是否允许修改

### 7.2 错误：根据文件名猜测功能

**场景**：看到 `Compressor/control.py`，仅凭文件名假设其功能或控制算法

**正确做法**：
- 查阅 `PLAN.md §2`，确认 Compressor 控制逻辑属于 Step5
- 如果当前是 Step3，**不修改** Compressor/
- 在 `STRUCTURE.md §2` 中查看当前职责与验证状态，并以实际代码为准

### 7.3 错误：假设"已完成 Step"正确

**场景**：Step3 使用 `sensor_checked[]`，假设 Step2 已正确实现

**正确做法**：
- 用 `read` 实际查看 Sensor/validate.py 代码
- 检查是否有质量码逻辑
- 如果有问题，**报告而不是修改**

### 7.4 错误：忘记 MicroPython 限制

**场景**：使用 `time.time()` / `json.dump(..., indent=2)`

**正确做法**：
- 查阅 `AI_DEVELOPMENT_RULES.md §3.2` 与 `DESIGN.md` 平台说明
- 使用 `time.ticks_ms()` / `time.ticks_diff()`
- json 保持最简参数

### 7.5 错误：直接读 sensor_data 用于控制

**场景**：在 Alarm 规则中 `if state.sensor_data[IDX_CURRENT] > 50:`

**正确做法**：
```python
# 先查质量和年龄
if state.sensor_quality[IDX_CURRENT] != state.Q_OK:
    return  # 传感器故障，退出判定
if state.sensor_age_ms(IDX_CURRENT) > 1000:
    return  # 数据超龄，退出判定

# 质量门禁通过后才读 checked
current = state.sensor_checked[IDX_CURRENT]
if current > I_MAX:
    # 触发过流保护
```

---

## 8. 示例：正确的开发流程

### 场景：实现 Step3 Alarm 高压保护

#### 1. 阅读文档
- ✅ 读 AI_DEVELOPMENT_RULES.md（本文档）
- ✅ 读 `PLAN.md §1`（当前进度）+ `§2` Step3 DoD
- ✅ 读 `DESIGN.md §6`（保护规则）+ `§5`（启停时序）
- ✅ 读 `STRUCTURE.md §2` Alarm/ 说明

#### 2. 确认范围
- ✅ 当前 Step3，可以修改 Alarm/
- ✅ 需要创建 `Alarm/rules.py` 和 `Alarm/supervisor.py`
- ✅ 需要在 main.py 添加 supervisor 协程
- ❌ 不修改 Sensor/validate.py（Step2）
- ❌ 不修改 HAL/Driver.py（Step4）

#### 3. 检查现有代码
```bash
# 查看 state.py 是否已定义需要的常量
read Board/state.py | grep DISCHARGE_PSI

# 查看 sensor_checked 是否可用
read Sensor/validate.py | head -50

# 查看 error_reg 定义
read Board/state.py | grep error_reg
```

#### 4. 实现代码
- 创建 `Alarm/rules.py`：纯函数，返回故障建议
- 创建 `Alarm/supervisor.py`：状态机 + error_reg 写入
- 修改 `main.py`：添加 supervisor 协程

#### 5. 编写测试
- 创建 `test/test_step3_alarm.py`
- 覆盖全部 DoD 项
- 运行测试

#### 6. 提交报告
- 列出修改文件
- 说明每个问题如何修复
- 提供测试结果
- DoD 自检

---

## 9. 快速决策表

| 情况 | 行动 |
|------|------|
| 不确定是否在 Step 范围 | ⏸️ 停止，向用户询问 |
| 发现已完成 Step 的 P0 问题 | ⏸️ 停止，报告问题 |
| 需要跨 Step 修改 | ⏸️ 停止，说明原因 |
| 代码与文档不一致 | ⏸️ 停止，报告冲突 |
| MicroPython 兼容性不确定 | ⏸️ 停止，查阅文档 |
| 修改 state 寄存器变量 | ✅ 添加 `global` 声明 |
| 控制逻辑读传感器 | ✅ 先查质量码和年龄 |
| 添加新传感器 | ✅ 先更新 state.py 索引 |
| 修改 PID 算法 | ❌ 禁止（已有注释） |
| 重构已完成 Step | ❌ 禁止（除非 P0） |

---

## 10. 违规检查

**修改完成后，用以下清单自查是否违规**：

- [ ] 我修改了不在当前 Step 范围的文件
- [ ] 我重构了已完成 Step 的代码
- [ ] 我修改了 HAL/PID_Plus.py 的算法
- [ ] 我未按当前权威四区点表更新 Modbus 地址或 CSV 契约
- [ ] 我在控制逻辑中直接读 sensor_data
- [ ] 我在 validate.py 中执行了保护动作
- [ ] 我在 reads() 中删除了 finally 块
- [ ] 我创建了新的协程在 validate 中
- [ ] 我使用了不兼容 MicroPython 的 API
- [ ] 我假设了未经验证的"已完成功能"

**如果任何一项为"是"，立即停止并重新审查。**

---

**最后更新**：2026-08-30

# 项目计划、进度与问题台账

**用途**：唯一的进度与问题真相来源。查"现在做到哪、下一步做什么、还有什么问题"只看本文档。
**最后核对**：2026-10-11（确认 P2-6/P2-7 口径；明确当前样机为空冷直膨、无水回路；同步 DESIGN/点表的样机与后续扩展边界）

- 验收报告详情 → `REPORT.md`
- 设计规范 → `DESIGN.md`
- 目录与数据面 → `STRUCTURE.md`

---

## 1. 当前状态速览

| 项 | 值 |
|---|---|
| **当前位置** | Step1-6 已封板；**Step7 代码 DoD 完成**（2026-09-17）；**实测 DoD 待目标机** |
| **测试** | 329/329 PASS（Step2 25 / Step3 30 / Step4 23 / Step5a 27 / Step5b 56 / Step6 96 / Step7 72）|
| **进入 Step5a 的判定** | ✅ **已完成并封板** —— P0-2 已解决，CRITICAL-2 已修复，综合评分 94/100 |
| **进入 Step5b 的判定** | ✅ **已完成并封板** —— 三环控制已实现，CRITICAL-1/MAJOR-1 已修复，综合评分 98/100 |
| **进入 Step6 的判定** | ✅ **已完成并封板** —— 参数单源、寄存器全量映射、B6/C8/A6/C4/C5/D-8/MINOR-1 全部闭环，综合评分 100/100 |
| **未解决 P0** | 0 项 |
| **未解决 P1** | 0 项（B6 周期失配、C8 传感器故障单 bit 均已修复）|
| **未解决 P2** | 5 项（P2-1~P2-5），均不阻塞 |
| **未决设计缺口** | **0 项** —— 所有设计决策已拍板并落地 |
| **实测缺口** | 全部目标机实测延后：Modbus 总线（Step7 主机脚本已备）、ticks_us 阻塞、Pico soak、参数掉电保持（PC 已模拟）|
| **变更风险** | 无重大代码变更风险；目标机实测尚未完成，不能据 PC 回归结果替代硬件验收 |

---

## 2. Step 计划与 DoD

依赖：1→2→3 严格串行；4 在 5 前；**5a→5b 串行**；6 可与 5b 并行；7 含原 Step1 的 Modbus 实测关门。

Step5 原定义只覆盖"开机"（软启+晋级），不含运行期控制逻辑。v2.5 拆为 **5a 设备执行层** 与 **5b 系统主逻辑**。Step6/7 编号不变（代码注释与测试有引用）。

| 步 | 内容 | 代码 DoD | 实测 DoD | 状态 |
|---|---|---|---|---|
| **1** | P0 修复包：A3 ccp / B2 `_read_frame` 让出 / A2 modbus 进 gather / A4 raise | 4 项落地且范围未膨胀 | Modbus 总线+阀动 → **延后 Step7** | ✅ 代码完成 |
| **2** | 检查层：validate.py + state 三数组 + `sensor_age_ms` + reads try/finally + PID 质量门禁 | 质量码/fault_reg/PID 门禁符合 DESIGN §4.1；age 超限→Q_BAD | 拔线/毛刺注入（PC shim 完成，非目标机） | ✅ 封板 |
| **3** | Alarm + supervisor v1：延时表 + 状态机 + WDT + 手动复位 | 规则表+状态机+复位协议完整；error_reg 唯一写者 | 阈值注入（state 直写模拟） | ✅ 封板（后续启停时序修订纳入 Step5a） |
| **4** | AD7124 去阻塞 + Driver link（空载） | 阻塞预算表；link 协程 | ticks_us；Pico soak → **需目标机** | ✅ 封板 |
| **5a** | **设备执行层**：三设备消费 `supervisor_interlocks`；压缩机启动/晋级；ESC 遥测、流量与设备故障事件；启停时序、手动模式和转速反馈环 | interlocks 全键有消费者；S1-S6 启停路径；遥测/故障事件闭环；手动模式与 RPM 环开关按当前口径工作 | 无冷媒台架：启动/停止、急停、断链、遥测换算、故障注入、手动模式切换 | ✅ 代码与 PC 回归封板（2026-09-08）；目标机实测待完成 |
| **5b** | **系统主逻辑**：容量环（蒸发器出口空气温度 22°C）、冷凝压力环（12 bara）、过热度环、RUNNING 子状态与温控循环、防短循环、L1 限载、排气温度保护、L3 重试锁定、Modbus 断线停机；蒸发风机基础油门由用户设定，RPM 反馈调节由独立转速环开关控制 | 控制环方向/质量门禁、限载仲裁、温控时序、阈值校验、排温停机和重试锁定通过 PC 测试 | 无冷媒台架阶跃响应、温控循环、限载与保护响应待完成 | ✅ 代码与 PC 回归封板（2026-09-08）；台架实测待完成 |
| **6** | 参数单源、范围/一致性校验、持久化、转速环开关；Step6 当时完成旧映射验收，随后 Step7 按四区点表重构 | 参数映射/校验/持久化模拟通过；现行点位真相以 `MODBUS_POINT_TABLE.csv` / `MODBUS_ADDRESS_MAP.md` 为准 | Flash 实际掉电保持与写入次数待目标机 | ✅ 代码与 PC 回归封板（2026-09-15） |
| **7** | 收尾 + 标准四区 Modbus 总线总测；Display 更名/A8/孤儿清理 | 离线协议、异常码、广播、坏 CRC、手动 EXV 软件写入验证 | pymodbus 总线、4x223 EXV 脱阀阀动、目标机掉电保持待完成 | 🔶 代码完成（2026-09-17）；实测待目标机 |

### 2.1 各步验证方式

| 步 | 验证 |
|---|---|
| 1 | 代码审查；总线实测延后步7 |
| 2 | 故障注入：拔线、毛刺、reads 停更；质量码/fault/PID 保持（无 SAFE_STOP 要求） |
| 3 | 阈值/延时注入；复位再判定；STARTUP 不判吸气 LP；WDT |
| 4 | ticks_us 最大阻塞；Pico 24h soak |
| 5a | 目标机/无冷媒台架：启停时序、急停、断链、ESC 遥测换算、设备故障注入；手动模式 coils 0x2 切换并检查压缩机/冷凝风机/EXV；修改 4x220-223 验证手动设定和蒸发风机基础油门；转速环开关独立验证。流量规则仅做软件注入测试，当前样机无实体 DI |
| 5b | 当前空气侧样机/无冷媒台架：①容量环阶跃响应（蒸发器出口空气温度 → 压缩机转速）；②温控启停循环（达 setpoint 停机 → 回升超 HYST 重启）；③MIN_ON/MIN_OFF 计时；④L1 限载覆盖容量环；⑤控制环并行不互扰；⑥阈值单调性校验；⑦排气温度降容/停机；⑧L3 锁定/解锁；⑨Modbus 通讯断 60s → 正常停机。除霜和带水回路流量保护不属于当前样机验收范围 |
| 6 | 参数掉电保持；Flash 写次数；延时槽（4x100-107）可读写；ESC 遥测 3x207-3x222 抽检 |
| 7 | Modbus 总测：全 FC + 广播 + 坏 CRC；4x223 手动 EXV→脱开阀体阀动；999/di/ir 抽检 |
| 每步 | 有条件时 ≥2h soak：`gc.mem_free()` 无单调下降、无任务饿死 |

### 2.2 测试策略【已拍板 2026-08-26】

> Modbus 主机回归与板上总线联调统一放 Step7，不阻塞 Step2+ 编码。

| 项 | 约定 |
|---|---|
| Step2-6 使能手段 | 临时写 `state.state_data[ST_SYSTEM_ENABLE]=1` 或 REPL，不依赖屏 |
| Step3 复位/阈值 | 直接改 state / 调 supervisor API 注入；hr48 总线测归 Step7 |
| 安全 | Step3 前压缩机不带载；EXV 阀动测试必须脱开阀体 |
| 风险 | 末段才发现 Modbus 帧/CRC 问题 → Step7 预留联调时间，不删减 DoD |

---

## 3. 进度节点

| 日期 | 节点 | 结论 |
|---|---|---|
| 2026-08-25 | 架构方案 v2.3 落盘 | 全量审查 ~2690 行；A9/A10/A11 用户自修 |
| 2026-08-26 | Step1 阶段门 | 代码 DoD 通过；实测按决策 #26 延后 Step7 |
| 2026-08-26 | Step2 中断审查 | 工作区无 Step2 实质代码 → 判定从零重做 |
| 2026-08-30 | Step2 落地后审查 | **FAIL** —— A12/A13 阻塞，新登记 B6/C7/C8 |
| 2026-08-30 | Step2 返工完成 | PASS，24/24 测试；封板 |
| 2026-08-30 | Step3 完成 | PASS，30/30；EXV 架构违例同步修复 |
| 2026-08-30 | Step4 完成 | PASS，23/23；阻塞 760ms→~3ms（算术估算） |
| 2026-08-31 | Step1-4 完整审计 | **有条件通过**；登记 P0-1/P0-2/P1-1~4/P2-1~5 |
| 2026-08-31 | P0-1 方案拍板 | 全局 ADC 位宽注册表（仅 pyb 通道） |
| 2026-09-01 | 文档体系整理 + 问题状态复核 | P0-1 / P1-1 / P1-3 经代码核实**已修复** |
| 2026-09-03 | 历史问题修复完成 | P0-1/P1-1/P1-3 已验证，文档 SYSTEM_LOGIC_DESIGN.md 已修复（蒸发风机相关），回归测试 77/78 通过 |
| 2026-09-03 | Step3 返工完成 | 启动时序修订为 S1 冷凝风机→S2 蒸发风机→S3 EXV→S4 延时后压缩机；新增 delay_slots[8] 延时槽；测试 29/30 通过 |
| 2026-09-03 | 数据结构扩展完成 | state_data 扩展到 32 元素；新增 ST_ESC0-3 遥测索引（16-31）；新增 esc_telemetry_cfg/manual_params/evap_fan_user_params 参数组；main.py DriverLink 增加 ESC 遥测采集；回归测试 77/78 通过 |
| **2026-09-07** | **Step5a 开发完成** | 三设备模块实现完成；转速环架构落地；压缩机/风机故障检测完成；手动模式实现；测试 27/27 PASS |
| **2026-09-07** | **Step5a 黑箱审计** | 发现 BLOCKER-1（压缩机故障检测缺失）、CRITICAL-1（遥测倍率错误）、CRITICAL-2（反馈镜像混淆）、MAJOR-2/3（手动模式安全） |
| **2026-09-07** | **Step5a 缺陷修复** | BLOCKER-1/CRITICAL-1/MAJOR-2/3 已修复；回归测试 105/105 PASS；综合评分 88→94/100 |
| **2026-09-08** | **CRITICAL-2 修复完成** | supervisor/rules 统一读取 ST_MOTOR_SPEED；架构合规性验证通过；数据流完整性闭环 |
| **2026-09-08** | **Step5a 正式封板** | 所有阻塞性缺陷已修复；测试 105/105 全部通过；P0-2 根治；架构设计优势已实现；**可进入 Step5b** |
| **2026-09-08** | **Step5b 开发完成** | 容量环/冷凝压力环实现；L1限载规则6条；温控循环R1-R4；L3重试锁定；排气温度保护；Modbus通讯断处理；测试 56/56 PASS |
| **2026-09-08** | **Step5b 黑箱审计（第一轮）** | 发现 CRITICAL-1（容量环反向逻辑）、MAJOR-1（温控质量门禁缺失）；测试盲区（闭环响应未测试）；综合评分 94/100 |
| **2026-09-08** | **Step5b 缺陷修复** | CRITICAL-1/MAJOR-1 已修复；新增测试用例4个（闭环响应+质量门禁）；回归测试 161/161 PASS |
| **2026-09-08** | **Step5b 黑箱审计（第二轮）** | 验证修复完成；数据流完整性闭环；架构合规性通过；综合评分 94→98/100 |
| **2026-09-08** | **Step5b 正式封板** | 所有阻塞性缺陷已修复；测试 161/161 全部通过；三环控制稳定；测试/代码比1.12；**可进入 Step6** |
| **2026-09-15** | **Step6 开发完成** | 寄存器层以 `MODBUS_POINT_TABLE_COMPLETE.csv` 重写（hr0-93/ir0-49/di0-31）；参数单源 + 范围/一致性校验；B6/C8/A6/C4/C5/D-8 闭环；DESIGN §9.4 持久化落地；**MINOR-1 转速环使能开关显式消费（hr69-71）**；新增 test_step6 用例；回归全绿 |
| **2026-09-17** | **Step7 代码 DoD 完成** | `Desplay.py`→`Display.py` 更名（D3）；A8 哨兵 999 原样透传；孤儿清单全部销账（§5）；死代码按决策仅标记不删除；DESIGN §8.1 手动模式映射对齐 Step6；新增 `test/test_step7_verification.py` 离线协议测试 + `test/test_step7_pymodbus_host.py` 上板脚本；**实测 DoD（pymodbus 总线 + 掉电保持）待目标机** |

---

## 4. 问题台账

状态说明：🔴 未修复且阻塞 / 🟡 未修复不阻塞 / ✅ 已修复并验证 / ⏸ 已知延后

### 4.1 P0 —— 原阻塞性问题（已全部修复）

| ID | 问题 | 位置 | 状态 |
|---|---|---|---|
| **P0-2** | **`supervisor_interlocks` 无消费者 → 启动序列必然失败**。S1 等 `ST_VALVE_POS` 到 `EXV_START_POS`，但 Valve_CRTL 从不读 interlocks；S2/S3 等风机 RPM，但 Condenser/Evaporator 是空壳；S4/S5 同理。任何启动尝试 → S1 超时 10s → `_fail_startup` → 锁存 EXV_FAULT → SAFE_STOP → 手动复位 → 再失败，死循环 | `Alarm/supervisor.py` S1-S6；三设备模块 | ✅ **已修复**（Step5a）|

**修复内容**：
- `Compressor/control.py`：转速环实现，消费 `comp_enable` / `comp_throttle_cmd`（supervisor.py）；公共接口由 `Compressor/__init__.py` 导出
- `Evaporator/control.py`：转速环实现，消费 `evap_fan_enable` / `evap_fan_throttle_cmd`；公共接口由 `Evaporator/__init__.py` 导出
- `Condenser/control.py`：转速环实现，消费 `cond_fan_enable` / `cond_fan_throttle_cmd`；公共接口由 `Condenser/__init__.py` 导出
- `EXV/Valve_CRTL.py`：消费 `exv_enable` / `exv_target_pct`
- **全部 interlocks 键已有消费者，S1-S6 启动时序可真实走通**

#### 已修复的原 P0

| ID | 问题 | 修复证据 | 状态 |
|---|---|---|---|
| **P0-1** | 电流 ADC 位宽不匹配：`pyb.ADC.read()` 返 12-bit 但按 `/65535` 换算 → 读数恒钳到 0.0A 且 quality=Q_OK → **过流保护永不触发**（A13 失效模式复现） | `state.py:325-330` 建 `adc_bits` 注册表；`ADC_read.py:33` `read_current(adc, bits=)`；`read_sensors.py:77,133` 显式传 `bits=state.adc_bits["PA4_I"]`；`psi_trans` 保持 `/65535` 不动（adc_direct 通道） | ✅ 已修复 |

### 4.2 P1

| ID | 问题 | 位置 | 状态 |
|---|---|---|---|
| **B6** | **周期失配**：`RATE_LIMITS` / `FRESHNESS_MS=1000` 按标称 200ms 标定，实测墙上周期 ≈760-850ms。Step4 去阻塞后事件循环不再饿死，但**墙上时钟不变**（AD7124 转换物理时间）→ 阈值偏紧约 4 倍，正常斜坡误判 SUSPECT；新鲜度余量仅 ≈150-200ms，任何抖动即全通道 BAD → PID 间歇冻结 | `validate.py:31,49`；`Valve_CRTL.py:125` 同为 1000ms（两处重复定义未单源） | ✅ **已修复（Step6）**：`FRESHNESS_MS_GLOBAL=3400`、`RATE_LIMITS`×4，`Valve_CRTL.py` 引用同一常量 |

**B6 修复方案（Step6 实施）**：
- **方案 A：重新标定阈值**（推荐）
  - `FRESHNESS_MS`: 1000ms → **3400ms**（实测 800ms × 4 + 安全余量 200ms）
  - `RATE_LIMITS`: 按 800ms 周期重新计算（原值 × 4）
  - 单一来源：`validate.py` 定义 `FRESHNESS_MS_GLOBAL`，`Valve_CRTL.py` 引用
  - 优点：简单直接，无需代码架构变更
  - 缺点：如果实际周期波动较大（760-850ms），仍可能误判
- **方案 B：动态测量周期**
  - 运行时测量实际采样周期（`ticks_ms()`）
  - 动态归一化 `RATE_LIMITS`
  - 优点：自适应，鲁棒性高
  - 缺点：需要增加运行时计算和状态维护
- **推荐**：方案 A（简单可靠），如果实测仍有问题再考虑方案 B
| **C8** | **`ST_SENSOR_FAULT` 覆盖而非汇总**：`state_data[ST_SENSOR_FAULT] = 0 if sensor_fault_reg == 0xFFFF else 1`，只有 1 bit，无法区分哪路传感器。原设计 `sensor_fault_reg` 已是 16-bit 分通道寄存器，ir 应映射 16 个离散位 | `supervisor.py:460` | ✅ **已修复（Step6）**：保留 `ST_SENSOR_FAULT` 汇总位兼容，`ir12` 映射完整 16-bit `sensor_fault_reg`（分通道） |

方向安全性：B6 误判方向是 SUSPECT/BAD（偏保守），不会把真故障误判为 OK，故不阻塞。

#### 已修复的原 P1

| ID | 问题 | 修复证据 | 状态 |
|---|---|---|---|
| **P1-1** | `driver_link_ok` 永不更新 → 恒为初值 True，Step5 开启 `LINK_CHECK_ENABLE` 后链路断检测失效 | `main.py:124` `state.driver_link_ok = link_ok`；消费方 `rules.py:158,164` + `supervisor.py:491` | ✅ 已修复 |
| **P1-3** | `supervisor.tick()` 异常仅打印后继续 → **所有保护规则停止评估**，但 sys_mode 冻结、外观"正常"，操作员看不到 SAFE_STOP | `supervisor.py:542-557`：异常后 `_enter_safe_stop("tick 异常")`，并有二级兜底强制 `MODE_SAFE_STOP` + 禁用全部执行机构 | ✅ 已修复 |
| **CRITICAL-2** | **压缩机反馈镜像混淆**：Compressor 模块镜像 `ST_ESC0_RPM → ST_MOTOR_SPEED`，但 supervisor S4/S5 和 rules 直接读 `ST_ESC0_RPM`，数据源不统一，未实现软件层抽象隔离 | `supervisor.py:279,294` / `rules.py:210` | ✅ **已修复**（Step5a 封板前）|

**CRITICAL-2 修复内容**：
- supervisor.py:281,296：改为读取 `ST_MOTOR_SPEED`（已修复）
- rules.py:215：改为消费 `ST_COMPRESSOR_FAULT` 事件位（已修复）
- 架构优势已实现：硬件层（ST_ESC0_RPM）→ 软件层（ST_MOTOR_SPEED）→ 业务层（supervisor/rules/容量环）

### 4.3 P2 —— 功能缺陷/边界条件，不阻塞

| ID | 问题 | 位置 | 状态 |
|---|---|---|---|
| **P2-1** | `SmoothedADC(window=16)` × 实测周期 800ms = **12.8s 有效滤波时间常数**；叠加 `HP_DELAY_S=3s` → 高压保护总响应 ≈15.8s，远超 3s 设计意图。建议 window=4 或按 dt 归一化 | `board.py:51-52` | 🟡 未修复 |
| **P2-2** | 派生质量 age 显示新鲜但 quality=BAD：父通道 BAD 时 SH/E quality 正确升级，但 `checked_ms` 仍是最近一次 OK 时刻 → Modbus 显示"age=0.2s, quality=BAD"混淆操作员（PID 双查行为正确） | `validate.py:133-140` | 🟡 未修复 |
| **P2-3** | 低 SH 判定读 `sensor_data`（raw）而非 `checked`，违反§7"控制通道永远看 checked"；且§7.3 级4 要求"**持续** < -5K"，此处为瞬时判定 | `validate.py:143-145` | 🟡 未修复 |
| **P2-4** | `gc.collect()` 在 async 方法内同步调用，MicroPython 下可能阻塞 10-50ms，破坏"<5ms 阻塞预算"。Step4 测试 10 只做算术估算，未实测 gc 影响 | `AD7124_PT100.py:147,184` | 🟡 未修复 |
| **P2-5** | **传感器超龄延时双倍**：`_sensor_stale_cond` 用 `min_age > SENSOR_STALE_DELAY_S*1000` 作条件，规则表再等同一个 `SENSOR_STALE_DELAY_S` → 配 3s 实际 6s 才触发。根因是混淆"年龄阈值"与"规则延时"。修法：条件改用已存在的 `AGE_LIMIT_MS`（=2000） | `rules.py:194,201` | 🟡 未修复 |
| **P2-6** | 转速环的设计目的为转速约束/保护；执行层将基础油门映射成 `rpm_cmd`，启用时由 RPM 反馈 PID 调节 ESC 油门。三个转速环使能开关独立保留，手动模式下仍由操作者选择开环/闭环，不因手动模式强制开或关；关闭时开环且无转速反馈约束。该口径确认“保护目的”与“闭环实现”并存，按实现更新 DESIGN，不改代码 | `Compressor/control.py`；`Condenser/control.py`；`Evaporator/control.py`；DESIGN.md §3.3/§4.2/§7/§8.2 | ✅ 已拍板并同步（2026-10-11） |
| **P2-7** | 电流传感器 BAD 动作采用现有实现：`CURRENT_BAD_ACTION=0` 为仅告警（条件消失自动清），`=1` 为延时停机并锁存。修改 DESIGN 注释与规则说明追认该语义，不改代码 | `Alarm/rules.py`；`Alarm/supervisor.py`；DESIGN.md §6.5/§9.2 | ✅ 已拍板并同步（2026-10-11） |
| **S2-1** | 死代码：`if sh_q > 当前quality` 成立时 `sh_q ≥ 1`，内层 `if sh_q == Q_OK` 永不可达 | `validate.py:132-139` | 🟡 未修复 |
| **杂项** | `driver_link_ok` 在 `state.py:318` 与 `:349` **重复定义两次**（后者覆盖前者，值相同故无害），建议合并 | `state.py` | 🟡 未修复 |

### 4.4 历史问题（已闭环）

| ID | 问题 | 修复 |
|---|---|---|
| A1 | `board.adc_pc2.sample()` 属性名错误 | ✅ 改 adc_pc2c |
| A2 | modbus 两循环未进 gather → 使能恒 0，EXV/PID 全灭 | ✅ Step1 |
| A3 | `sync_holding_readback` 中 `ccp` 未定义，首轮 NameError | ✅ Step1 |
| A4 | `raise -999` / `raise 9999`（对整数 raise） | ✅ Step1 改 ValueError |
| A5 | 压力哨兵→TypeError→`update_all_sensor_faults` 被跳过 | ✅ Step2 try/finally + 单通道 try |
| A9 | EXV TMC2209 `uart_id=2`，实际 UART7 | ✅ 用户自修 |
| A10 | Driver `UART_ID=2`，实际 UART6 | ✅ 用户自修 |
| A11 | board 越层代建 AD7124 实例（import 即初始化，失败拒绝启动） | ✅ 方案 B：board 只留总线/片选，实例由 read_sensors 惰性创建 |
| A12 | `set_sensor_fault`/`clear_sensor_fault` 缺 `global` → `UnboundLocalError` 从 finally 逸出 → **validate 一次都执行不到，quality 恒 Q_BAD，EXV 永不动作** | ✅ Step2 返工，state.py:115,120 |
| A13 | `IDX_CURRENT` 未采样却判 Q_OK（0∈[0,50]）→ 过流保护看到"新鲜有效的 0A"静默失效；`read_current()` 无 return | ✅ Step2 返工：sensor_data 初值 999 + 实现 read_current + reads 补采样 |
| B2 | Modbus `_read_frame` 总线噪声饿死事件循环 | ✅ Step1：有数据 `sleep_ms(0)`，空闲 `sleep_ms(2)` |
| B1 | AD7124 同步阻塞 ~700ms/周期，会饿死 Pico 100ms 超时→ESC 油门清零 | ✅ Step4 async 化，阻塞降至 ~3ms |
| C7 | 升级 BAD 时清零 `_suspect_cnt` + SUSPECT 更新 `_prev_value` → quality 三态振荡 `1,2,0,1,2,0…` 从不锁存 BAD，破坏 Step3 全部延时计时器 | ✅ Step2 返工：仅 Q_OK 时清零计数器 |
| P2(S2-6) | SUSPECT 把毛刺写入 `sensor_checked` 并污染 `_prev_value` | ✅ Step2 返工 |
| P1-5 | 新鲜度检查误判初次读数（timestamp=0 即判 BAD） | ✅ Step2 返工：仅检查已更新过的通道 |
| EXV 架构违例 | `Valve_CRTL.py`/`EXV.py` 直接 `set_fault('EXV_FAULT')`，绕过延时表与手动复位协议，违反 error_reg 单写者 | ✅ Step3：EXV 只写 `ST_VALVE_FAULT` 事件位，新增 EXV_FAULT 规则走标准延时管线 |
| BLOCKER-1 | 压缩机故障检测缺失：Compressor 模块从不写入 `ST_COMPRESSOR_FAULT` 事件位 → rules.COMPRESSOR_FAULT 规则失效 | ✅ Step5a：新增 ST_COMPRESSOR_FAULT 索引33；Compressor/__init__.py:103-107 实现故障检测 |
| CRITICAL-1 | ESC 遥测倍率错误：voltage_scale/current_scale=0.01 导致反馈值缩小100倍 | ✅ Step5a：state.py:410,413,416,419 修正为 1.0 |
| MAJOR-2 | 手动模式覆盖时机不一致：Compressor/Condenser 在 STARTUP 期间也会被手动模式覆盖 | ✅ Step5a：增加 sys_mode==MODE_RUNNING 条件（Compressor/__init__.py:65, Condenser/__init__.py:59）|
| MAJOR-3 | EXV 手动模式缺少安全检查：STOPPING 期间手动模式会阻止 EXV 关闭 | ✅ Step5a：Valve_CRTL.py:123 改为仅 MODE_RUNNING 允许 |

### 4.5 待修但已明确归属

| ID | 问题 | 归属 |
|---|---|---|
| A6 | `PID_Plus` save/load 用 `encoding=`/`indent`，MicroPython 不支持 | ✅ Step6 已完成 |
| A7 | `Desplay.py` `zip(sensor_data(10), REGS(9))` 静默截断，电流永不上屏 | ⏸ Step7 决策跳过（测试屏无权威第 10 项地址规格，不猜测） |
| A8 | 999×100=99900 溢出 u16 → 屏显 343.64 | ✅ Step7 已修（`Display.py` `_scale()` 哨兵 999 原样透传）|
| B3 | `valve_loop` 移动失败时增量已清零，丢失不重试 | 后续 |
| B5 | `EXV.py` import 期创建 `_motor`，上电即占 UART/TIM，无法"EXV 故障但其余继续启动" | ⏸ Step7 决策保留（仅标记，未惰性化）|
| C4 | 参数三源（state 默认/config.json/pid_params.json）互不打通 | ✅ Step6 已完成（state 单源 + `Board/params_store.py` 统一 config.json）|
| C5 | Modbus 写入无范围校验（Kp 可写负数/327.67） | ✅ Step6 已完成（`_HR_RANGE` + 一致性校验，违反返回 0x03）|
| C6 | 运行中 0→1→0→1 不重新归零，丢步无修正（`check_and_rehome` 被注释） | 后续 |
| D3/D5 | Desplay 命名拼写错误；死代码占比高含误导性引脚信息 | ✅ D3 Step7 已修（更名 `Display.py`）；D5 死代码按决策仅标记不删除（STRUCTURE 已标注） |
| S2-5 | `psi_trans` 上边沿返回 28.42 bara > 量程上限 26.0 → 判 BAD。**Step6 设 `HP_MAX` 必须 < 26.0**，否则真实高压落进 BAD 而非触发高压保护 | ✅ Step6 已确认（`HP_MAX=22.0 < 26.0`）|
| D-8 | `FRESHNESS_MS` 与 `Valve_CRTL.py:125` 的 1000ms 重复定义，未单源 | ✅ Step6 已完成（单源 `validate.FRESHNESS_MS_GLOBAL`）|

### 4.6 不确定点

| ID | 内容 | 现状 |
|---|---|---|
| U1 | `pyb.ADC` 位宽 | ✅ 已查官方文档确认 `ADC.read()` 返 0-4095（12-bit）；`pyb.ADC` **无** `read_u16()`；16-bit 路线可走 `pyb.ADCAll(16, mask).read_channel()` 或 `machine.ADC.read_u16()` —— 待目标机验证 H743 支持 |
| U2 | `adc_direct` 返回值范围 | ✅ 间接证据齐全指向 16-bit（`psi_trans` 按 65535 换算、`_is_adc_out_of_range` 默认 65535、测试 mock 用 32768）。固件冻结模块无法直接审源码，建议 REPL 验证 `print(adc_direct.read_pc2_c())` |
| U3 | `gc.collect()` 阻塞时长 | 未实测，需目标机 `ticks_us()` |
| U4 | Pico 侧 100ms/2s 超时实际行为 | Pico 侧代码未审查 |

### 4.7 设计缺口（v2.5 补设计，**2026-09-02 已全部拍板**）

两处此前既无代码也无设计的空白，已补入当前权威设计文档 `DESIGN.md` §5-§7，相关设计决策于 **2026-09-02 逐条拍板完成**。

| ID | 缺口 | 原状 | 补设计位置 | 拍板状态 |
|---|---|---|---|---|
| **G1** | **报警动作细节缺失** | 11 条规则只有 3 个动作码，除低过热度外全部直接 `ACT_SAFE_STOP`，**无"限载/降容"这类跳闸前的中间动作** —— 高压/过流等本可通过降容自救的工况直接跳闸 | §9 全节重写：L0-L3 分级、7 个新动作码、分级阈值表、并发仲裁、恢复/复位策略、重试锁定、防短循环 | ✅ 已拍板 |
| **G2** | **`error_reg` 7 个孤儿位** | 位 1/2/3/6/8/9/11（压缩机、蒸发风机、冷凝风机、排温、LCD 通讯、Modbus 通讯、配置 IO）**已定义但无任何规则**，永远不会置位 | §9.4 第三张表，逐位给出条件/延时/级别/动作 | ✅ 已拍板 |
| **G3** | **系统主逻辑缺失** | `MODE_RUNNING` 只查 ENABLE 和 error_reg。**无容量控制、无冷凝压力控制、无温控循环**。原 Step5 只覆盖"开机" | 新增系统主逻辑：RUNNING 子状态 R1-R4、三环解耦、温控循环、三种停机路径；当前空冷直膨样机不含除霜 | ✅ 已拍板 |
| **G4** | **被控变量未定义** | `compressor_control_params` 注释明写"具体含义由 Compressor 模块自己解释"，`setpoint=20.0` 的单位与物理量均未定义 | §16.1 给出 4 个候选方案与推荐 | ✅ 已拍板 |

**已拍板结论汇总（2026-09-02）**：

| # | 问题 | 拍板结论 |
|---|---|---|
| **Q1** | 排气温度是否做保护？ | **做 L1 降容 + L2 延时停机**：`TD_UNLOAD=105°C` 触发降容（不低于 `COMP_OIL_RETURN_RPM`），`TD_TRIP=115°C` 连续不间断 300s 停机。守住"不做瞬时排温停机"，但给压缩机自保 |
| **Q2** | L1 限载是否也走延时？ | **是**，L1 走延时槽（默认槽 1 = 5s），比对应 L2 短，形成"先限载后跳闸"时间梯度 |
| **Q3** | 重试锁定 L3 是否要做？ | **做**，报警条件消失即清 `retry_lockout`（非手动复位清），error_reg 锁存位仍需手动复位 |
| **Q4** | Modbus 通讯断是否 fail-safe 停机？ | **延时 60s 后正常停机**（不锁存 error_reg），恢复后 ENABLE 即可重启，避免频繁手动复位 |
| **Q5** | 流量报警位 | `FLOW_FAULT` 规则为后续带水回路版本预留；当前空冷直膨样机无水回路、无实体流量 DI，`FLOW_ENABLE` 保持 0。点位沿用 `error_reg` bit15 / 1x16 |
| **Q6** | 被控变量是什么？ | **蒸发器出口空气温度 `IDX_OUTLET_T`**（索引 3） |
| **Q7** | `setpoint=20.0` 的单位与物理量 | **°C**，蒸发器出口空气温度目标，触摸屏 Modbus 可读写；当前设为 **22.0°C** |
| **Q8** | 系统类型 | **风冷式直膨制冷；蒸发侧直膨，无水/载冷剂回路**；PT100 包括入口/出口空气、压缩机吸排气及制冷剂液管温度 |
| **Q9** | 是否需要除霜？ | 当前样机不实现除霜；需限定并验证不结霜的运行范围，低温/高湿扩展工况另行设计 |
| **Q10** | 是否要制热模式？ | **不做**，硬件无四通阀，按纯制冷设计；制热属二期且需加硬件 |
| **Q11** | `HP_TARGET` 与 `setpoint` 初值 | **`HP_TARGET=12.0 bara`**（冷凝压力环日常目标）、**`setpoint=22.0°C`**，台架可调 |

**Q6-Q8 解除 Step5b 阻塞**，可开工容量环与主逻辑。

---

## 4.8 报警分级动作落地矩阵【2026-09-02 新增】

将 `DESIGN.md` §6 报警分级设计逐项拆分到 Step5a/5b/6/7，明确每条规则、动作码、error_reg 位的实现归属与验收标准。

### 4.8.1 延时槽与 error_reg 扩展（Step5a 前置）

| 项 | 内容 | 归属 | DoD |
|---|---|---|---|
| 延时槽 | `state.py` 新增 `delay_slots[8] = [0, 5, 10, 30, 45, 60, 300, 900]`（秒） | Step5a | 8 个槽值可由 Modbus 读写（Step6 寄存器映射） |
| error_reg 扩展 | `state.py` `error_reg` 改为 32-bit；`FLOW_FAULT` 当前使用 bit 15（初始方案 bit 16，点表重构时调整） | Step5a | `set_fault`/`clear_fault`/`is_fault` 兼容 32-bit；测试覆盖最终位号 |
| 流量事件位（后续扩展） | `ST_FLOW_SWITCH`（索引 32）和 `di_flow` 软件接口已预留；当前 `di_flow` 是恒为正常的模拟对象，不是实体 DI | 后续带水回路版本 | 接入实体开关后再做拔线/断水实测；当前保持 `FLOW_ENABLE=0`，软件规则注入测试不视为实物保护验收 |

### 4.8.2 ESC 遥测接入（Step5a）

| 项 | 内容 | 归属 | DoD |
|---|---|---|---|
| 数据面扩展 | `state_data` 扩展到 32 元素，索引 16-31 存 ESC0-3 遥测（erpm/temp/voltage/current × 4） | Step5a | 32 个索引常量定义；`state_data = [0] * 32` |
| 配置 | `ESC_TELEMETRY_CFG[4]` 定义各 ESC 倍率与有效位；本机 ESC1/2 current=False | Step5a | 配置表在 `state.py`；各通道倍率台架标定前占位 |
| link 协程 | `main.py` link 协程遍历 4 通道，按配置工程换算后写入 `state_data[16-31]`；无效通道写 999 | Step5a | link 协程每周期刷新；测试验证 ESC0-2 遥测写入、ESC1/2 current=999 |

### 4.8.3 L1 动作码（Step5b）

| 动作码 | 名称 | 消费方 | 归属 | DoD |
|---|---|---|---|---|
| `ACT_INHIBIT_UP` | 禁升速 | 容量环 clamp 输出到当前转速 | Step5b | 容量环实现 clamp 逻辑；测试触发后转速不上升 |
| `ACT_UNLOAD` | 降容 | 容量环强制按 `UNLOAD_RATE_RPM_S` 下降到 `COMP_OIL_RETURN_RPM` | Step5b | 降容覆盖容量环输出；测试降速速率 |
| `ACT_COND_FAN_MAX` | 冷凝风全速 | 冷凝风机强制全速，覆盖压力环输出 | Step5b | 冷凝风机执行；测试高压限载时风机全速 |
| `ACT_NORMAL_STOP` | 正常停机 | 走 §8.3.2 正常停机序列（不锁存 error_reg） | Step5b | Modbus 通讯断触发；测试恢复后 ENABLE 可重启 |

### 4.8.4 L1 限载规则（Step5b）

| 规则 | 条件 | 延时槽 | 动作 | 归属 | DoD |
|---|---|---|---|---|---|
| 高压限载 | 排气压力 > `HP_UNLOAD=20.5` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_COND_FAN_MAX` | Step5b | 阈值注入测试；验证降容+风机全速并行 |
| 电流禁升 | 电流 > `I_LIMIT=40.0` | 槽 1 (5s) | `ACT_INHIBIT_UP` | Step5b | 阈值注入测试；验证转速 clamp |
| 电流降容 | 电流 > `I_UNLOAD=42.0` | 槽 1 (5s) | `ACT_UNLOAD` | Step5b | 阈值注入测试；验证降速 |
| 低压限载 | 吸气压力 < `LP_UNLOAD=2.8` | 槽 1 (5s) | `ACT_UNLOAD` | Step5b | 阈值注入测试；验证降容抬高吸气压力 |
| 临界低过热 | SH < `SH_CRIT=1.0` | 槽 1 (5s) | `ACT_UNLOAD` + `ACT_EXV_REDUCE` | Step5b | 阈值注入测试；验证降容+关小 EXV |
| 排气温度降容 | 排气温度 > `TD_UNLOAD=105` | 槽 1 (5s) | `ACT_UNLOAD`（下限 `COMP_OIL_RETURN_RPM`） | Step5b | 阈值注入测试；验证不低于回油转速 |

### 4.8.5 L2 新增规则（Step5a/5b）

| 规则 | error_reg 位 | 条件 | 延时槽 | 归属 | DoD |
|---|---|---|---|---|---|
| 压缩机故障 | 1 | 使能且有油门指令，但 `state_data[ST_ESC0_RPM]` ≈0；或 S4 软启失败 | 槽 1 (5s) | Step5a | ESC0 遥测消费；S4 验证；测试锁存 |
| 蒸发风机故障 | 2 | 使能且有油门指令，但 `state_data[ST_ESC1_RPM]` 偏离 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a | ESC1 遥测消费；L1→L2 两段（先降容，延时未恢复停机） |
| 冷凝风机故障 | 3 | 使能且有油门指令，但 `state_data[ST_ESC2_RPM]` 偏离 > `FAN_RPM_TOL` | 槽 1 (5s) | Step5a | ESC2 遥测消费；L1→L2 两段 |
| 排气温度停机 | 6 | 排气温度 > `TD_TRIP=115` **连续不间断** | 槽 6 (300s) | Step5b | 中途降温重新计时；测试持续 300s 停机 |
| Modbus 通讯断 | 9 | 无有效 Modbus 帧超 `MODBUS_TIMEOUT_S` | 槽 5 (60s) | Step5b | `ACT_NORMAL_STOP` 不锁存；恢复后 ENABLE 重启 |
| 流量故障（后续扩展） | 当前 bit15 / 1x16 | 带水回路版本实体 DI 断开 | 槽 2 (10s) | 后续扩展 | 规则代码可注入测试；当前样机无实体 DI，保持 `FLOW_ENABLE=0` |

### 4.8.6 重试锁定 L3（Step5b）

| 项 | 内容 | 归属 | DoD |
|---|---|---|---|
| `retry_lockout` | 同一故障 1 小时内锁存 3 次 → 置 `retry_lockout`，禁止自动重启 | Step5b | `state.py` 新增标志；supervisor 维护计数器（按位分别计数） |
| 解锁条件 | 报警条件消失即清 `retry_lockout`；error_reg 锁存位仍需手动复位 | Step5b | 测试故障消失后 `retry_lockout=False`，但 error_reg 需 hr48 复位 |
| 窗口衰减 | 超 `RETRY_WINDOW_S=3600` 自动清计数 | Step5b | 测试 1 小时后计数清零 |

### 4.8.7 执行端接线（Step5a）

P0-2 的根本问题：`supervisor_interlocks` 无消费者。Step5a 必须补齐三设备模块的执行端：

| 模块 | 消费 interlocks 键 | 实现内容 | 归属 | DoD |
|---|---|---|---|---|
| `Compressor/control.py` | `comp_enable`、`comp_throttle_cmd` | 读 interlocks，使能 ESC0，写入油门；软启 S4 验证 `ST_ESC0_RPM` 到达目标 | Step5a | S1-S6 真实走通；急停测试；interlocks 全键有消费者（静态扫描） |
| `Evaporator/control.py` | `evap_fan_enable`、`evap_fan_throttle_cmd` | 读 interlocks，使能 ESC1，写入油门 | Step5a | S2/S3 风机启动；断链测试 |
| `Condenser/control.py` | `cond_fan_enable`、`cond_fan_throttle_cmd` | 读 interlocks，使能 ESC2，写入油门 | Step5a | S2 风机启动；高压限载时 `ACT_COND_FAN_MAX` 覆盖 |
| `EXV/` | `exv_enable`、`exv_target_pct` | 读 interlocks（**零消费，全新开发**；2026-09-07 勘误：原文"已部分实现"失实），含 L1 `ACT_EXV_REDUCE` 消费 | Step5a | S1 阀位到达；L1 关小 EXV 测试 |

### 4.8.8 接口口径勘误（2026-09-07）

1. **§4.8.5 位 1/2/3 条件**：原文 `state_data[ST_ESCx_ERPM]` 索引不存在，实际遥测写入 `ST_ESC0_RPM`/`ST_ESC1_RPM`/`ST_ESC2_RPM`（`Driver.update_telemetry_to_state()`）；判定条件为"使能且有油门指令但反馈转速异常"。
2. **§4.8.7 EXV 行**：原文"已部分实现"失实——grep 全仓库证实 `EXV/` 对 `supervisor_interlocks` 零消费，S3 阀位判定会超时失败。EXV 消费属全新开发。
3. **转速环口径更新（2026-10-11）**：supervisor interlocks 保持油门键名（`*_throttle_cmd`）与启动油门参数不变；外环/手动设定输出基础油门，执行层映射为 `rpm_cmd`，启用转速环时读取 `ST_ESCx_RPM` 并由 PID 闭环调节 ESC 油门。转速环的设计目的为转速约束/保护，但实现不局限于接近上限时的单向限幅。三个独立开关保留给操作者，手动模式下不强制切换。

---

## 5. 孤儿参数清单（验收口径：每行销账才算"参数在 main 里体现"）【2026-09-17 Step7 销账】

| 定义 | 缺口 | 归属 | 状态 |
|---|---|---|---|
| `ST_SYSTEM_ENABLE` 写通道 | 代码已通（A2），总线实测延后 | Step7 | 代码 ✅（hr0 → `modbus_slave.py:261`）；总线实测归目标机 |
| `ST_MOTOR_SPEED`、`ST_*_FAN_RPM/FAULT` | Modbus ir[20-26] 每周期读，永远读到 0 | Step5a | ✅ 已实现（写者 `Compressor/control.py:203`、`Evaporator/control.py:105`、`Condenser/control.py:190`；`modbus_slave.py:512-516` 每周期读）|
| `ST_SENSOR_FAULT` | 仅单 bit，信息丢失（C8） | Step6 | ✅ 已实现（ir12 完整 sensor_fault_reg）|
| `compressor_control_params` | `Compressor()` 是 pass；hr14-23 可写但无下游 | Step5b | ✅ 已实现（`Compressor/control.py` 消费 hr16-24；`modbus_slave.py:284-293` 写入）|
| `ST_MOTOR/FAN_SPEED_MIN/MAX` 限幅 | 旧记 hr26-29，实为 hr72/hr77；无控制环消费 | Step5b | ✅ 已实现（`ST_MOTOR_SPEED_MAX`←hr72→`Compressor/control.py:63`；`ST_FAN_SPEED_MAX`←hr77→`Condenser/control.py:63`/`Evaporator/control.py:34`；`ST_MOTOR_SPEED_MIN`→`supervisor.py:798` R4 判据）。**残留**：`ST_FAN_SPEED_MIN` 无 Modbus 入口且无消费者（见 §5.1）|
| `supervisor_interlocks` 全部键 | 无消费者（P0-2） | Step5a | ✅ 已实现（消费者：`Compressor/control.py:143`、`Evaporator/control.py:54`、`Condenser/control.py:132`、`EXV/Valve_CRTL.py:122`）|
| `holding_regs[8/9/10]`（阀位回读/手动模式/手动阀位） | `_apply_holding_writes` 未实现，纯纸面 | Step6 | ✅ 已实现（hr9 回读/hr10 手动使能/hr15 手动 EXV）|
| `config.json` / `pid_params.json` | 加载逻辑被注释；目标机从不加载 | Step6 | ✅ 已实现（`Board/params_store.py` 统一 config.json）|
| `discrete_inputs`（FC02） | 同步代码已写，循环未启动 | Step6/7 | ✅ 代码已实现（di0-di31，总线实测归 Step7；离线 FC02 已测）|
| **ESC 遥测**（`state_data[16-31]`） | Driver `EscStatus`（erpm/温度/电压/电流）无消费者 | **Step5a** | ✅ 已实现（`Compressor/control.py:177,202`、`Evaporator/control.py:80,104`、`Condenser/control.py:165,189` 消费；`modbus_slave.py:531-533` ir33-48 映射）|
| **延时槽**（`delay_slots[8]`） | 各报警规则延时参数独立，触摸屏配置繁琐 | **Step5a/6** | ✅ 已实现（hr50-hr57 可读写）|
| **流量保护**（后续带水回路版本，error_reg bit15 / 1x16） | 当前样机没有水回路；`board.di_flow` 是软件占位，不是实体 DI | 后续扩展 | 规则代码可模拟测试；当前不可宣称有实物流量保护，`FLOW_ENABLE` 必须保持 0 |

### 5.1 残留登记【2026-09-17】

| 定义 | 说明 | 处置 |
|---|---|---|
| `ST_FAN_SPEED_MIN`（state_data[14]） | 内部默认 300 rpm，无 Modbus 写入入口，设备模块仅用 `throttle_min` + `rpm_max` 限幅，无消费者 | 保留占位；如需风机最低转速限制，后续步新增 hr 与消费逻辑 |
| `board.REGS` | 历史遗留，STRUCTURE 标注应移交 `Display_Touch` | Step7 决策仅标记不迁移（见 STRUCTURE §2） |
| `HAL/PWM.py`、`Sensor/tem_lastV02_STM32.py`、`EXV.py` 顶部注释块 | 死代码/误导性引脚信息 | Step7 决策仅标记不删除（见 STRUCTURE §1） |

---

## 6. 进入 Step5a/5b 的行动清单

### 6.1 Step5a 封板确认【2026-09-08 已完成】

✅ **Step5a 已正式封板**

**封板验证清单**：
- ✅ P0-2 已解决：三设备模块消费 interlocks 完整
- ✅ BLOCKER-1 已修复：压缩机故障检测补全
- ✅ CRITICAL-1 已修复：ESC 遥测倍率修正
- ✅ CRITICAL-2 已修复：数据源统一（supervisor/rules 读 ST_MOTOR_SPEED）
- ✅ MAJOR-2/3 已修复：手动模式安全检查
- ✅ 测试通过：105/105 PASS（Step5a 27 + 回归 78）
- ✅ 综合评分：94/100（达到封板标准）
- ✅ 架构合规性：单一写者、延时表、质量门禁均正确
- ✅ 数据流完整性：ESC 遥测闭环、故障检测链路完整

**Step5a 交付物**：
- 三设备模块转速环实现（Compressor/Evaporator/Condenser）
- error_reg 扩展到 32-bit（新增 ST_COMPRESSOR_FAULT 位33）
- 延时槽机制（8个槽）
- ESC 遥测工程换算（state_data[16-31]）
- 压缩机/风机故障检测规则
- 手动模式实现（hr9/11-14）
- 启停时序可真实走通（S1-S6）
- 软件层抽象（ST_MOTOR_SPEED 数据源统一）

### 6.2 Step5b 开工前置【2026-09-08 已就绪】

✅ **Step5b 可立即开始**

**前置条件验证**：
- ✅ Step5a 封板完成
- ✅ Q1-Q11 设计决策已全部拍板
- ✅ 转速环架构已明确：基础油门映射为 RPM 目标，开关开启时反馈 PID 调节；手动模式下开关仍由操作者选择
- ✅ 三环控制设计已完整（容量环/冷凝压力环/过热度环）
- ✅ L1 限载规则已明确（6条规则 + 4个动作码）
- ✅ 数据源已统一（容量环读 ST_MOTOR_SPEED）

**Step5b 实施清单**：
1. 容量环实现（蒸发器出口空气温度 → 压缩机 throttle_base，10s 周期）
2. 冷凝压力环实现（排气压力 → 冷凝风机 rpm_cmd，2s 周期）
3. RUNNING 子状态实现（R1-R4）
4. L1 限载动作码实现（ACT_INHIBIT_UP/UNLOAD/COND_FAN_MAX/NORMAL_STOP）
5. L1 限载规则实现（6条规则）
6. 温控循环实现（MIN_ON/MIN_OFF/THERMO_HYST）
7. 排气温度多阶段保护（L1降容 + L2停机）
8. L3 重试锁定实现
9. Modbus 通讯断正常停机
10. 分级阈值单调性校验

### 6.3 原行动清单【已过时，保留历史记录】

---

## 7. 安全与兼容性铁律

1. **安全**：Step3 前压缩机不得带冷媒/带载；EXV 阀动测试必须脱开阀体
2. **Modbus 点位契约稳定**：遵守当前标准四区点表；已有地址变更须先更新权威点表并确认兼容性
3. **999 哨兵原样透传**保留；质量码 ir10/11、报警 di0-15 为新增，旧屏不绑也不破坏
4. **压力全程绝压 bara**，不得再引入表压阈值
5. **全延时**：禁止实现"条件边沿瞬时停机"快捷路径（与 Pico 100ms 兜底不冲突，链路层 vs 应用层）
6. **不推翻**：PID 算法、TMC 时序、已确认的 Modbus 四区地址、state 索引、现有顶层目录
7. **平台**：整机 H743；F407 遗留注释 Step7 清理
8. **保护停机不受最短运行时间约束**（v2.5）：`MIN_ON_S` / `MIN_OFF_S` 只约束温控停机与操作员停机，**绝不阻塞 L2/L3 保护停机**（§9.7）
9. **分级阈值单调性**（v2.5）：同一物理量必须满足 `warn < unload < trip`，supervisor 启动时校验，违反则拒绝启动并置 `CONFIG_IO_FAULT`（§9.3）
10. **L1 限载优先于容量环**（v2.5）：降容指令覆盖容量环输出，容量环只能请求更低值不能反向抬升（§9.5）

| **2026-09-15** | **Step6 黑箱审计** | 独立审计验证参数消费完整性（238 个点位）；发现 MINOR-1（转速环使能开关未显式检查）；综合评分 98/100 |
| **2026-09-15** | **MINOR-1 修复完成** | Compressor/Condenser/Evaporator 三模块显式检查 SPEED_LOOP_ENABLE（hr69-71）；行为逻辑与 DESIGN.md 完全一致；验证通过 |
| **2026-09-15** | **Step6 正式封板** | 参数消费率 97.9%（233/238）；Modbus 全量映射 100%（hr0-93/ir0-49/di0-31）；所有关键问题已修复（B6/C8/A6/C4/C5/D-8/MINOR-1）；综合评分 100/100；**可进入 Step7** |

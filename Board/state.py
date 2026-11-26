#state.py
"""
全局共享状态 —— 所有协程/模块通过 import 本模块读写,
禁止在各文件里各自维护同名局部变量。
"""

import time

# ────────────────────────────────────────────────────────────
# 质量码定义 (Q 数值越大越差；派生通道取父通道 max 时直接使用整数比较)
# ────────────────────────────────────────────────────────────
Q_OK      = 0
Q_SUSPECT = 1
Q_BAD     = 2

# ────────────────────────────────────────────────────────────
# 传感器原始数据 (10 元素, 由 Sensor 协程 read_sensors.reads() 刷新)
# 下标含义 —— 与 Sensor/read_sensors.py 中的赋值顺序严格一致
# ────────────────────────────────────────────────────────────
IDX_SUCTION_T     = 0   # 回气温度 (PT100)
IDX_DISCHARGE_T   = 1   # 排气温度 (PT100)
IDX_INLET_T       = 2   # 蒸发器入口空气温度 (PT100)
IDX_OUTLET_T      = 3   # 蒸发器出口空气温度 (PT100，容量环被控量)
IDX_LIQUID_T      = 4   # 液管温度 (PT100)
IDX_SUCTION_PSI   = 5   # 低压压力 (ADC 表压探头，软件绝压 bara)
IDX_DISCHARGE_PSI = 6   # 高压压力 (ADC 表压探头，软件绝压 bara)
IDX_SUPERHEAT     = 7   # 回气过热度 SH (suction_t - 饱和温度)
IDX_E_VALUE       = 8   # 查表所得焓值 E (Extensions.read_r134a.read_E)
IDX_CURRENT       = 9   # 整机电流 (board.adc_current 为主采样，adc_backup 为备用/冗余采集)

# 初始化为哨兵值 999，防止未采样通道被误判为「有效的 0」
sensor_data = [999] * 10

# 过冷度（派生通道，2026-09-19 Modbus 重构新增，映射 3x9）
# 计算：饱和温度(排气压力) - 液管温度；无法计算时 999
subcooling = 999

# 液管焓值（派生通道，2026-09-22 新增，映射 3x11 出口焓）
# 计算：read_E(排气压力, 液管温度)；无法计算时 999
# 用途：冷凝器出口焓，计算制冷量 Q = m*(h_吸气 - h_液管) 与 COP
liquid_enthalpy = 999

# 校验后数据面：控制通道专用 (PID/Alarm/supervisor 先查 quality 与年龄再取值)
sensor_checked    = [0.0] * 10   # 校验后数值；BAD 时保留最后已知 good 值
sensor_quality    = [Q_BAD] * 10 # 上电初始为 BAD，首次 validate 后再定
sensor_checked_ms = [0] * 10     # 最近一次 Q_OK 更新时刻 (ticks_ms)

def sensor_age_ms(idx):
    """距该通道最近一次 Q_OK 更新的毫秒数；未更新过则返回大正数"""
    t = sensor_checked_ms[idx]
    if t == 0:
        return 10**9
    return time.ticks_diff(time.ticks_ms(), t)

# 运行状态数据（由各自协程刷新，单一写者）
# 压缩机转速反馈（r/min）。由 Compressor 模块从 ST_ESC0_RPM 镜像，
# 作为 supervisor 启停验收与后续容量环的统一数据源。
ST_MOTOR_SPEED        = 0
 
# EXV 阀门 —— 只读镜像，供 Display / Modbus 等模块查询当前开度。
# EXV.py 内部的 pending_delta / valve_busy 生产者-消费者状态仍由
# EXV 模块自己维护，不在这里暴露；EXV 每次动作成功后应把最新位置
# 同步写回 state.state_data[ST_VALVE_POS] / [ST_VALVE_POS_STEPS]。
ST_VALVE_POS          = 1    # 当前开度 (百分比 0.0~100.0)
ST_VALVE_POS_STEPS    = 2    # 当前开度 (步数 0~500)
ST_VALVE_FAULT        = 3    # EXV/TMC2209 驱动器故障 (check_health() 失败)，0/1
 
# 风机 —— 蒸发器/冷凝器均为 DShot 控制，转速指令/反馈格式与
# 压缩机一致，统一用 r/min
ST_EVAP_FAN_SPEED_CMD = 4    # 蒸发器风机 DShot 转速指令 (r/min)
ST_EVAP_FAN_RPM       = 5    # 蒸发器风机转速反馈 (r/min)
ST_EVAP_FAN_FAULT     = 6    # 蒸发器风机转速异常/堵转标志，0/1
 
ST_COND_FAN_SPEED_CMD = 7    # 冷凝器风机 DShot 转速指令 (r/min)
ST_COND_FAN_RPM       = 8    # 冷凝器风机转速反馈 (r/min)
ST_COND_FAN_FAULT     = 9    # 冷凝器风机转速异常/堵转标志，0/1
 
ST_SENSOR_FAULT       = 10   # 任一传感器读数命中故障哨兵值时置位，0/1

ST_SYSTEM_ENABLE      = 11   # 系统启动开关：0=停机 1=运行
                              # 压缩机/风机/EXV等主要部件启动前必须先检查此位
                              # 可由 Modbus/组态屏写入，也可由安全联锁逻辑强制置0

# ────────────────────────────────────────────────────────────
# 转速限幅 —— 保护性限制，压缩机/风机的实际输出转速不允许超出
# 这个范围。具体数值由硬件规格书/调试确定，这里先给占位默认值。
# ⚠ 上线前必须用真实压缩机/风机规格校准，避免超转速损坏设备。
# ────────────────────────────────────────────────────────────
ST_MOTOR_SPEED_MIN    = 12   # 压缩机最低转速限制 (r/min)
ST_MOTOR_SPEED_MAX    = 13   # 压缩机最高转速限制 (r/min)
ST_FAN_SPEED_MIN      = 14   # 风机最低转速限制 (r/min)，蒸发器/冷凝器共用
ST_FAN_SPEED_MAX      = 15   # 风机最高转速限制 (r/min)，蒸发器/冷凝器共用

# Step5a: 流量开关 DI 状态（1=正常通水，0=水流断开）
ST_FLOW_SWITCH        = 32

# Step5b: RUNNING 子状态（ir14 映射）
RUNNING_PULLDOWN   = 0   # R1: 降温中，容量环全力
RUNNING_MODULATING = 1   # R2: 接近设定，容量环调节
RUNNING_LIMITED    = 2   # R3: L1 限载中
RUNNING_THERMO_OFF = 3   # R4: 达到设定，压缩机停

running_substate = RUNNING_PULLDOWN

# Step5b: 动作码位图（supervisor 设置，控制环/时序消费）
# 全部使用不重叠的位，便于组合表达同一规则多个动作。
ACT_INHIBIT_UP   = 0x01   # 禁升速：容量环只许降不许升
ACT_UNLOAD       = 0x02   # 降容：强制下降到回油油门
ACT_COND_FAN_MAX = 0x04   # 冷凝风机强制全速
ACT_EXV_REDUCE   = 0x08   # 关小 EXV
ACT_NORMAL_STOP  = 0x10   # 正常停机（不锁存 error_reg）
ACT_SAFE_STOP    = 0x20   # 故障停机
limit_actions = 0

# Step5b: 重试锁定状态
retry_lockout = False

# Step6: 启动阶段镜像（ir30，0-6 对应 S0-S6；由 supervisor 刷新）
startup_stage = 0

# Step6: 执行机构实际输出镜像（ir17-24 只读展示，由设备模块刷新）
exv_target_pct = 0.0          # EXV 当前目标开度 %（Valve_CRTL 写）
comp_throttle_out = 0         # 压缩机实际下发油门（Compressor 写）
evap_fan_throttle_out = 0     # 蒸发风机实际下发油门（Evaporator 写）
cond_fan_throttle_out = 0     # 冷凝风机实际下发油门（Condenser 写）

# Step5b: 外环输出（控制环 -> supervisor_interlocks 的缓冲）
# supervisor 是 supervisor_interlocks 唯一写者；容量环/冷凝压力环
# 只写这些基础油门变量，由 supervisor 在 RUNNING 期间复制到 interlocks。
comp_throttle_base = 0       # 容量环输出（油门绝对值）
cond_fan_throttle_base = 0   # 冷凝压力环输出（油门绝对值）

# ────────────────────────────────────────────────────────────
# ESC 遥测数据（Step5a 新增，2026-09-03）
# 16-31: 4 个 ESC × 4 字段（rpm/temp/voltage/current）
# 由 main.DriverLink() 协程从 Driver.UartLink.statuses[] 读取并换算
# 999 表示无效数据（链路断、ESC 无响应等）
# ────────────────────────────────────────────────────────────
# ESC0（压缩机）
ST_ESC0_RPM           = 16   # 压缩机反馈转速 (rpm)
ST_ESC0_TEMP          = 17   # ESC 温度 (°C)
ST_ESC0_VOLTAGE       = 18   # 母线电压 (×10 V，如 240 = 24.0V)
ST_ESC0_CURRENT       = 19   # 相电流 (×10 A，如 150 = 15.0A)

# ESC1（蒸发风机）
ST_ESC1_RPM           = 20   # 蒸发风机反馈转速 (rpm)
ST_ESC1_TEMP          = 21   # ESC 温度
ST_ESC1_VOLTAGE       = 22   # 母线电压
ST_ESC1_CURRENT       = 23   # 本机无效，固定 999（电流由 ACS712-50A 统一采样）

# ESC2（冷凝风机）
ST_ESC2_RPM           = 24   # 冷凝风机反馈转速 (rpm)
ST_ESC2_TEMP          = 25   # ESC 温度
ST_ESC2_VOLTAGE       = 26   # 母线电压
ST_ESC2_CURRENT       = 27   # 本机无效，固定 999

# ESC3（备用）
ST_ESC3_RPM           = 28   # 备用 ESC 反馈转速 (rpm)
ST_ESC3_TEMP          = 29   # ESC 温度
ST_ESC3_VOLTAGE       = 30   # 母线电压
ST_ESC3_CURRENT       = 31   # 相电流

# Step5a: 压缩机故障事件位（供 Alarm/rules.py COMPRESSOR_FAULT 规则消费）
ST_COMPRESSOR_FAULT   = 33

state_data = [0] * 34   # Step5a 扩展：16 → 32（新增 ESC 遥测），ST_FLOW_SWITCH=32，ST_COMPRESSOR_FAULT=33

# 占位默认值，需按压缩机/风机实际规格书校准
state_data[ST_MOTOR_SPEED_MIN] = 1500
state_data[ST_MOTOR_SPEED_MAX] = 6000
state_data[ST_FAN_SPEED_MIN]   = 300
state_data[ST_FAN_SPEED_MAX]   = 3000

# ESC 遥测初始化为无效值（999）
for i in range(16, 32):
    state_data[i] = 999

# 水流开关默认正常（1），由 reads() / 测试注入刷新
state_data[ST_FLOW_SWITCH] = 1


# ────────────────────────────────────────────────────────────
# 传感器故障寄存器 —— 16位, 每个传感器通道单独占一位
# 1 = 正常, 0 = 异常, 全部正常时 sensor_fault_reg == 0xFFFF
#
# 只覆盖"直接测量"的传感器 (5路PT100 + 2路压力 + 1路电流)，
# 过热度/焓值是计算值，不在这里占位，异常与否由上游传感器位反映。
# 8~15 位预留，以后加传感器往后排即可。
# ────────────────────────────────────────────────────────────
SENSOR_FAULT_BITS = {
    "SUCTION_T_FAULT":     (0, IDX_SUCTION_T),
    "DISCHARGE_T_FAULT":   (1, IDX_DISCHARGE_T),
    "INLET_T_FAULT":       (2, IDX_INLET_T),
    "OUTLET_T_FAULT":      (3, IDX_OUTLET_T),
    "LIQUID_T_FAULT":      (4, IDX_LIQUID_T),
    "SUCTION_PSI_FAULT":   (5, IDX_SUCTION_PSI),
    "DISCHARGE_PSI_FAULT": (6, IDX_DISCHARGE_PSI),
    "CURRENT_FAULT":       (7, IDX_CURRENT),
}

 
sensor_fault_reg = 0xFFFF   # 16位寄存器；bit=1 正常，bit=0 异常
 
def set_sensor_fault(name):
    global sensor_fault_reg
    bit, _ = SENSOR_FAULT_BITS[name]
    sensor_fault_reg &= ~(1 << bit) & 0xFFFF

def clear_sensor_fault(name):
    global sensor_fault_reg
    bit, _ = SENSOR_FAULT_BITS[name]
    sensor_fault_reg |= (1 << bit)

def update_all_sensor_faults(fault_value=999):
    """在 reads() 里调用一次，自动根据 sensor_data 当前值刷新所有故障位"""
    for name, (bit, idx) in SENSOR_FAULT_BITS.items():
        if sensor_data[idx] == fault_value:
            set_sensor_fault(name)
        else:
            clear_sensor_fault(name)

# 运行时控制参数(软件默认值启动,屏幕连接后由 param_sync 覆盖同步)

# EXV 过热度环 PID 参数，控制对象是 EXV 开度
control_params = {
    "Kp": 0.6, "Ki": 0.12, "Kd": 3.0,
    "setpoint": 5.0, "error_threshold": 2.0,
    "deadband": 0.2, "max_delta": 8.0,
    "aggr_mode": "togoal",
    "tau": 3.0, "T_goal": 5.0, 
}

# ────────────────────────────────────────────────────────────
# Step5b: 压缩机容量环 PID 参数（外环：蒸发器出口空气温度 → 基础油门）
# 字段与 HAL/PID_Plus.IncrementalController 兼容。
# ────────────────────────────────────────────────────────────
compressor_control_params = {
    "Kp": 50.0,                    # 温度偏差 1K → 油门变化 50
    "Ki": 5.0,
    "Kd": 10.0,
    "setpoint": 22.0,              # 蒸发器出口空气温度目标 °C
    "error_threshold": 5.0,        # 大误差快速调节阈值
    "deadband": 0.5,               # 死区 ±0.5K
    "max_delta": 200.0,            # 单次最大变化 200 throttle/10s
    "aggr_mode": "togoal",
    "tau": 3.0,
    "T_goal": 10.0,
    "throttle_min": 48,            # 容量环输出下限（油门）
    "throttle_max": 2047,          # 容量环输出上限（油门）
    "period_s": 10.0,              # Step6: 容量环周期（hr24，可配）
}

# Step5b: 冷凝压力环 PID 参数（外环：排气压力 → 冷凝风机基础油门）
cond_control_params = {
    "Kp": 100.0,                   # 压力偏差 1 bara → 油门变化 100
    "Ki": 10.0,
    "Kd": 20.0,
    "setpoint": 12.0,              # 冷凝压力目标 HP_TARGET (bara)
    "error_threshold": 5.0,
    "deadband": 0.2,               # bara
    "max_delta": 150.0,            # 单次最大变化 150 throttle/2s
    "aggr_mode": "togoal",
    "tau": 3.0,
    "T_goal": 5.0,
    "throttle_min": 200,           # 压缩机运行期间必须排热
    "throttle_max": 2047,
    "period_s": 2.0,               # Step6: 冷凝压力环周期（内部使用）
}

# def load_control_params(path="config.json"):
#     """开机调用：用config.json覆盖默认值，缺的key保留默认值不报错"""
#     try:
#         import json
#         with open(path, "r") as f:
#             saved = json.load(f)
#         control_params.update({k: v for k, v in saved.items() if k in control_params})
#     except Exception as e:
#         print("[state] config加载失败，使用默认参数:", e)
#     return control_params

# def save_control_params(path="config.json"):
#     """把当前control_params落盘"""
#     try:
#         import json
#         with open(path, "w") as f:
#             json.dump(control_params, f)
#         return True
#     except Exception as e:
#         print("[state] config保存失败:", e)
#         return False

# ────────────────────────────────────────────────────────────
# 系统级故障寄存器 —— 16位, 对应 Modbus 离散输入 1x, 地址 0~15, FC02
# 每一位代表一路故障：1 = 正常，0 = 异常
# 全部正常时 error_reg == 0xFFFF；哪路故障就把对应位翻转成 0。
#
# 传感器通道本身的故障已经拆到上面的 sensor_fault_reg 里了，
# 这里只放"系统/保护"级别的故障 (EXV、风机、压缩机、通讯、
# 高低压保护等)，不再有笼统的 SENSOR_FAULT 位。
#
# ERROR_BITS 是我按项目里已有模块整理的草稿，目前代码里还没有
# 任何地方真正调用 set_fault()/clear_fault()，请按需增删/改名后
# 再统一接入各模块的故障判断逻辑。
# ────────────────────────────────────────────────────────────
# 2026-09-19 Modbus 重构：删除未使用的 LCD_COMM_FAULT（原 bit8），
# 原 bit9-16 整体前移为 bit8-15，使 di[i] 直接镜像 error_reg bit i，
# 与 docs/参考.csv 一致（1x9 Modbus通讯故障 … 1x16 流量保护）。
ERROR_BITS = {
    "EXV_FAULT":              0,   # EXV 归零失败 / TMC2209 驱动器故障 (通信失败/短路/过温/欠压)
    "COMPRESSOR_FAULT":       1,   # 压缩机启动失败/驱动通讯异常 (预留，压缩机驱动方案未定)
    "EVAP_FAN_FAULT":         2,   # 蒸发器风机 (DShot) 转速异常/无反馈
    "COND_FAN_FAULT":         3,   # 冷凝器风机 (DShot) 转速异常/无反馈
    "HIGH_PRESSURE_FAULT":    4,   # 高压保护 (排气压力超限)
    "LOW_PRESSURE_FAULT":     5,   # 低压保护 (吸气压力过低)
    "HIGH_DISCHARGE_TEMP":    6,   # 排气温度过高
    "OVERCURRENT_FAULT":      7,   # 整机电流过流保护
    "MODBUS_COMM_FAULT":      8,   # Modbus 通讯故障
    "LOW_SUPERHEAT_FAULT":    9,   # 过热度过低，存在液击风险
    "CONFIG_IO_FAULT":        10,  # config.json 读写失败 / 参数同步异常
    "SUCTION_PSI_FAULT":      11,  # 吸气压力传感器 BAD (§9 吸气压力传感器 BAD 规则)
    "DISCHARGE_PSI_FAULT":    12,  # 排气压力传感器 BAD (§9 排气压力传感器 BAD 规则)
    "CURRENT_SENSOR_FAULT":   13,  # 电流传感器 BAD (§9 电流传感器 BAD 规则)
    "SENSOR_STALE_FAULT":     14,  # 全通道超龄 / Sensor 协程心跳丢失 (§7.4 最后两行)
    "FLOW_FAULT":             15,  # 水流开关断开
}

# Driver 链路断 (§9 最后一行) 不占 error_reg 位（16 位已排满），
# 由 supervisor 维护 driver_link_latched 独立软标志（见 §15.4 报告），
# Modbus 侧映射到 1x100「Driver链路状态」。
 
error_reg = 0xFFFFFFFF   # 32位寄存器；bit=1 正常，bit=0 异常
 
def set_fault(name: str):
    """把 name 对应的位翻转成 0 (异常)"""
    global error_reg
    error_reg &= ~(1 << ERROR_BITS[name]) & 0xFFFFFFFF
 
def clear_fault(name: str):
    """把 name 对应的位恢复成 1 (正常)"""
    global error_reg
    error_reg = error_reg | (1 << ERROR_BITS[name])
 
def is_fault(name: str) -> bool:
    """查询 name 对应的位是否处于异常 (0)"""
    return not (error_reg & (1 << ERROR_BITS[name]))

# ────────────────────────────────────────────────────────────
# Step3: 系统状态机 (sys_mode)
# ────────────────────────────────────────────────────────────
MODE_BOOT        = 0  # 上电初始
MODE_SELF_TEST   = 1  # 外设自检（Step3 占位，Step7 实质）
MODE_SAFE_STOP   = 2  # 安全停机（默认待机）
MODE_STARTUP     = 3  # 启动时序 S0-S6
MODE_RUNNING     = 4  # 正常运行
MODE_ALARM       = 5  # 可恢复报警（继续运行）
MODE_STOPPING    = 6  # 停机时序
MODE_DEGRADED    = 7  # 自检失败（降级运行）

sys_mode = MODE_BOOT

# ────────────────────────────────────────────────────────────
# Step3: 保护参数（阈值 + 延时）
# Step6 经 Modbus 可读可写；Step3 代码先读此字典
# ────────────────────────────────────────────────────────────
protect_params = {
    # ── 压力分级阈值（绝压 bara）──
    "HP_MAX":    22.0,  # L2 停机
    "HP_UNLOAD": 20.5,  # L1 降容
    "HP_WARN":   19.0,  # L0 告警
    "HP_TARGET": 12.0,  # 冷凝压力环目标

    "LP_MIN":    2.5,   # L2 停机
    "LP_UNLOAD": 2.8,   # L1 降容
    "LP_WARN":   3.0,   # L0 告警
    "LP_HYST":   0.3,   # 低压恢复迟滞

    # ── 电流分级阈值（A）──
    "I_MAX":    45.0,   # L2 停机
    "I_UNLOAD": 42.0,   # L1 降容
    "I_LIMIT":  40.0,   # L1 禁升速
    "I_WARN":   38.0,   # L0 告警

    # 传感器年龄上限（ms，用于 supervisor 补充 age 检查）
    "AGE_LIMIT_MS":  2000,

    # Driver 链路检查使能（0=不检查，1=检查；Step4 前置 0）
    "LINK_CHECK_ENABLE":  0,

    # 电流传感器 BAD 动作（0=仅告警+禁升速，1=延时停机）
    "CURRENT_BAD_ACTION":  0,

    # ── 排气温度分级阈值（°C）──
    "TD_TRIP":   115.0,  # L2 连续 300s 停机
    "TD_UNLOAD": 105.0,  # L1 降容
    "TD_WARN":   95.0,   # L0 告警

    # ── 过热度阈值（K）──
    "SH_MIN":  2.0,   # L1 关小 EXV
    "SH_CRIT": 1.0,   # L1 降容

    # ── 延时（秒）──
    "HP_DELAY_S":                3.0,   # 高压 L2 延时
    "DISCHARGE_PSI_BAD_DELAY_S": 5.0,   # 排气压力传感器 BAD 延时
    "LP_DELAY_S":                10.0,  # 低压 L2 延时
    "SUCTION_PSI_BAD_DELAY_S":   10.0,  # 吸气压力传感器 BAD 延时
    "LSH_DELAY_S":               5.0,   # 低过热度延时
    "OC_DELAY_S":                3.0,   # 过流延时
    "CURRENT_BAD_DELAY_S":       5.0,   # 电流传感器 BAD 延时
    "LINK_LOSS_DELAY_S":         2.0,   # Driver 链路断延时
    "SENSOR_STALE_DELAY_S":      3.0,   # 传感器全通道超龄延时
    "EXV_FAULT_DELAY_S":         3.0,   # EXV 归零/驱动器故障延时（Step3 架构修复）

    # ── 延时槽（2026-09-03 新增，Step3 返工）──
    "delay_slots": [0, 5, 10, 30, 45, 60, 300, 900],  # 8 个延时槽（秒）
    "EXV_TO_COMP_DELAY_SLOT": 1,  # S3 EXV 到 S4 压缩机延时使用槽 1（5s）
    "COMP_STOP_DELAY_SLOT": 1,
    "FAN_STOP_DELAY_SLOT": 1,

    # Step5a/5b 新增：设备故障 / 流量故障 / L1 限载 / 排温延时槽号
    "COMPRESSOR_FAULT_DELAY_SLOT": 1,
    "EVAP_FAN_FAULT_DELAY_SLOT":   1,
    "COND_FAN_FAULT_DELAY_SLOT":   1,
    "FLOW_FAULT_DELAY_SLOT":       2,
    "L1_LIMIT_DELAY_SLOT":         1,   # L1 限载统一用槽 1（5s）
    "TD_TRIP_DELAY_SLOT":          6,   # 排气温度二阶停机 300s

    # ── 启停时序参数（保持油门值语义）──
    "EXV_START_POS":         30.0,  # 启动固定开度 (%)
    "COND_FAN_START_THROTTLE": 1000,  # 冷凝风机启动油门值 48-2047
    "EVAP_FAN_START_THROTTLE": 800,   # 蒸发风机启动油门值 48-2047
    "COMP_START_THROTTLE":   1200,  # 压缩机启动油门值 48-2047

    # 晋级模式：0=定速保持，1=开机延时
    "STARTUP_ADVANCE_MODE":  1,
    "COMP_HOLD_S":           10.0,  # 模式0：定速保持秒数
    "COMP_START_DELAY_S":    15.0,  # 模式1：开机延时秒数
    # Step6: 模式1 转速稳定性判据（A12：延时 + 稳定性双判据）
    "COMP_RPM_STABILITY_WINDOW_S": 10.0,  # 稳定性检查窗口（秒）
    "COMP_RPM_STABILITY_THRESHOLD": 100,  # 窗口内转速变化阈值（rpm）
    "COMP_RPM_STABILITY_SAMPLE_S": 1.0,   # 稳定性采样间隔（秒）

    # 转速反馈有效性判定（用于启动验收和设备故障检测）
    "RPM_VALID_MIN":         100,    # 转速反馈 > 100 rpm 视为有效（ESC 已启动）
    "FAN_RPM_TOL":           200,    # 风机转速容差

    "FAULT_FAN_HOLD_MS":     15000,  # 故障停机时风机保持毫秒数

    # 启动超时（每步允许最大毫秒数；超时→SAFE_STOP）
    "S1_TIMEOUT_MS":  10000,  # 冷凝风机
    "S2_TIMEOUT_MS":  10000,  # 蒸发风机
    "S3_TIMEOUT_MS":  10000,  # EXV 到位
    "S4_TIMEOUT_MS":  20000,  # 压缩机启动
    "S5_TIMEOUT_MS":  30000,  # 晋级判据

    # Step5a 新增：流量保护（默认禁用，待接入硬件）
    "FLOW_ENABLE": 0,

    # Step5b: 降容/回油参数
    "COMP_OIL_RETURN_THROTTLE": 200,   # 回油最低油门
    "UNLOAD_RATE_THROTTLE_S":   50,    # 降容速率 throttle/s

    # Step5b: 转速环使能开关（设备模块内读取）
    "COMP_SPEED_LOOP_ENABLE": 0,
    "COND_FAN_SPEED_LOOP_ENABLE": 0,
    "EVAP_FAN_SPEED_LOOP_ENABLE": 0,

    # Step5b: 防短循环与温控循环
    "MIN_ON_S": 180.0,            # 压缩机启动后最短运行时间
    "MIN_OFF_S": 300.0,           # 压缩机停机后最短再启动间隔
    "MAX_STARTS_PER_HOUR": 6,     # 每小时最大启动次数
    "THERMO_HYST": 2.0,           # 温控回差 K
    "MODULATING_THRESHOLD": 2.0,  # PULLDOWN -> MODULATING 偏差阈值 K
    "EVAP_FAN_THERMO_OFF_KEEP": 1,  # R4 期间蒸发风机保持

    # Step5b: 重试锁定
    "RETRY_MAX": 3,               # 窗口内最大重试次数
    "RETRY_WINDOW_S": 3600,       # 重试窗口 1 小时

    # Step5b: Modbus 通讯断检测
    "MODBUS_TIMEOUT_S": 60.0,     # 无有效 Modbus 帧超时
}

# ────────────────────────────────────────────────────────────
# Step3: 手动复位请求标志（Modbus 0x7 线圈脉冲写入此标志）
# ────────────────────────────────────────────────────────────
fault_reset_request = False  # supervisor 处理后复位为 False

# Step5b: Modbus 通讯断检测（modbus_slave.py 收到有效帧时更新）
last_modbus_frame_ms = 0

# ────────────────────────────────────────────────────────────
# P0-1 修复：全局 ADC 位宽注册表（仅 pyb/machine 模块内的 ADC 方法）
# adc_direct (PC2/PC3) 是项目自编译固件冻结模块，不在此表登记；
# psi_trans() 对 PC2/PC3 继续硬编码 /65535 换算。
# ────────────────────────────────────────────────────────────
ADC_BITS_DEFAULT = 12   # pyb.ADC.read() 默认 12-bit (0-4095)

adc_bits = {
    "PA4_I":     12,   # pyb.ADC(Pin('PA4')).read()  电流主采样 (P0-1 修复点)
    "PA5_I_BAK": 12,   # pyb.ADC(Pin('PA5')).read()  电流备用
}

# ────────────────────────────────────────────────────────────
# Step3/Step5a: supervisor 联锁指令（supervisor → 设备层）
# 按用户拍板：保持油门值语义；设备模块内部做油门下发调节器，不暴露 rpm 目标
# ────────────────────────────────────────────────────────────
supervisor_interlocks = {
    "comp_enable":          False,
    "comp_throttle_cmd":    0,      # 压缩机油门值 48-2047，停机为 0
    "evap_fan_enable":      False,
    "evap_fan_throttle_cmd": 0,     # 蒸发风机油门值 48-2047，停机为 0
    "cond_fan_enable":      False,
    "cond_fan_throttle_cmd": 0,     # 冷凝风机油门值 48-2047，停机为 0
    "exv_enable":           False,
    "exv_target_pct":       0.0,    # EXV 开度百分比 0.0-100.0
}

# ────────────────────────────────────────────────────────────
# P1-1 修复：Driver 链路状态（供 supervisor 链路断检测消费）
# ────────────────────────────────────────────────────────────
driver_link_ok = True   # 初值 True；由 main.py DriverLink() 协程更新

# ────────────────────────────────────────────────────────────
# Step5a: ESC 遥测配置（2026-09-03 新增；v3.2 扩展为完整配置表）
# 按 DESIGN.md §9.2 / §7.5：erpm/temp/voltage/current 有效位 + 倍率 + 极对数
# 无效通道写 999；ESC1/ESC2 current=False（由 ACS712-50A 统一采样）
# ────────────────────────────────────────────────────────────
def _build_esc_telemetry_params():
    """把 ESC_TELEMETRY_CFG 展开成 HMI 可读写的一维参数字典，键值与 points.csv 对应。"""
    cfg = [
        # ESC0 压缩机：Driver.py 帧解析已将 raw 值换算为实际 V/A，此处倍率保持 1.0
        {"erpm": True,  "temp": True,  "voltage": True,  "current": True,
         "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 3},
        # ESC1 蒸发风机 — 本机 current 无效
        {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
         "erpm_scale": 1.0, "voltage_scale": 2.1, "current_scale": 1.0, "pole_pairs": 7},
        # ESC2 冷凝风机
        {"erpm": True,  "temp": True,  "voltage": True,  "current": False,
         "erpm_scale": 1.0, "voltage_scale": 2.1, "current_scale": 1.0, "pole_pairs": 7},
        # ESC3 备用
        {"erpm": True,  "temp": True,  "voltage": True,  "current": True,
         "erpm_scale": 1.0, "voltage_scale": 1.0, "current_scale": 1.0, "pole_pairs": 7},
    ]
    out = {}
    for idx, item in enumerate(cfg):
        prefix = f"ESC{idx}_"
        out[prefix + "POLE_PAIRS"] = int(item["pole_pairs"])
        out[prefix + "ERPM_SCALE"] = float(item["erpm_scale"])
        out[prefix + "VOLTAGE_SCALE"] = float(item["voltage_scale"])
        out[prefix + "CURRENT_SCALE"] = float(item["current_scale"])
        out[prefix + "ERPM_ENABLE"] = 1 if item["erpm"] else 0
        out[prefix + "TEMP_ENABLE"] = 1 if item["temp"] else 0
        out[prefix + "VOLTAGE_ENABLE"] = 1 if item["voltage"] else 0
        out[prefix + "CURRENT_ENABLE"] = 1 if item["current"] else 0
    return cfg, out


ESC_TELEMETRY_CFG, esc_telemetry_params = _build_esc_telemetry_params()


def sync_esc_telemetry_cfg():
    """把 esc_telemetry_params 的当前值同步回 ESC_TELEMETRY_CFG，供 Driver.py 实时使用。"""
    for idx in range(len(ESC_TELEMETRY_CFG)):
        cfg = ESC_TELEMETRY_CFG[idx]
        prefix = f"ESC{idx}_"
        cfg["pole_pairs"] = int(esc_telemetry_params[prefix + "POLE_PAIRS"])
        cfg["erpm_scale"] = float(esc_telemetry_params[prefix + "ERPM_SCALE"])
        cfg["voltage_scale"] = float(esc_telemetry_params[prefix + "VOLTAGE_SCALE"])
        cfg["current_scale"] = float(esc_telemetry_params[prefix + "CURRENT_SCALE"])
        cfg["erpm"] = bool(esc_telemetry_params[prefix + "ERPM_ENABLE"])
        cfg["temp"] = bool(esc_telemetry_params[prefix + "TEMP_ENABLE"])
        cfg["voltage"] = bool(esc_telemetry_params[prefix + "VOLTAGE_ENABLE"])
        cfg["current"] = bool(esc_telemetry_params[prefix + "CURRENT_ENABLE"])


# ESC 调速油门边界：0 仅表示 disable；运行指令有效范围为 48-2047。
ESC_THROTTLE_MIN = 48
ESC_THROTTLE_MAX = 2047

# ────────────────────────────────────────────────────────────
# Step5a: 转速环 PID 参数（v3.2 双环控制：设备模块内部转速→油门闭环）
# 外环（容量环/冷凝压力环）输出目标转速，设备模块用以下参数做内环 PID。
# 为保持接口不变，supervisor_interlocks 仍使用 *_throttle_cmd 键名，
# 设备模块内部将油门值按 ESC_THROTTLE_MAX 映射到目标转速。
# ────────────────────────────────────────────────────────────
comp_speed_loop_params = {
    "Kp": 0.5,              # throttle/rpm（待台架标定）
    "Ki": 0.1,
    "Kd": 0.0,
    "period_s": 0.5,        # 0.5s 周期
    "rpm_max": 6000,        # 最大转速限制（hr72）
    "deadband": 200,        # ±200 rpm（hr75）
    "throttle_max_adjust": 100,  # 单次最大调节量（hr76）
}

fan_speed_loop_params = {
    "Kp": 0.3,              # throttle/rpm（待台架标定）
    "Ki": 0.05,
    "Kd": 0.0,
    "period_s": 0.5,        # 0.5s 周期
    "rpm_max": 3000,        # 最大转速限制（hr77）
    "deadband": 200,        # ±200 rpm（hr80）
    "throttle_max_adjust": 100,  # 单次最大调节量（hr81）
}

# ────────────────────────────────────────────────────────────
# Step5a/Step6: 手动模式参数
# Modbus hr9/hr11-14 映射，Step6 实现
# 按用户拍板：保持油门值语义，不改成转速；设备模块内部映射到目标转速
# ────────────────────────────────────────────────────────────
manual_params = {
    "MANUAL_MODE_ENABLE": 0,        # 0=自动 / 1=手动（控制压缩机/冷凝风机/EXV）
    "MANUAL_COMP_THROTTLE": 1000,      # 手动压缩机油门值 48-2047，停机为 0
    "MANUAL_COND_FAN_THROTTLE": 1000,  # 手动冷凝风机油门值 48-2047，停机为 0
    "MANUAL_EXV_PCT": 30.0,         # 手动 EXV 开度 (%)
}

# ────────────────────────────────────────────────────────────
# Step5a: 蒸发风机用户设定
# 对应 Modbus hr[221]（4x222），RUNNING 期间始终生效（不受 MANUAL_MODE_ENABLE 控制）
# 按用户拍板：保持油门值语义；设备模块内部映射到目标转速
# ────────────────────────────────────────────────────────────
evap_fan_user_params = {
    "EVAP_FAN_USER_THROTTLE": 1500,  # 默认油门值 1500（对应 hr[221]，即 4x222）
}

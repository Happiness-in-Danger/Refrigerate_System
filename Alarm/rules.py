# Alarm/rules.py
"""
保护规则纯函数表 (Step3)

依据：ARCHITECTURE_PLAN.md §9 + §8.3.4/8.3.5
- 所有报警→动作必须经延时（无零延时跳闸）
- 质量非 OK 时数值类规则条件判 False（延时清零）
- 每条规则自有 *_DELAY_S 和武装表（按 sys_mode）
- validate 只判定标记，不执行动作；supervisor 是 error_reg 唯一写者
"""

import time
import Board.state as state

# ═══════════════════════════════════════════════════════════
# 动作码（supervisor 执行）
# Step5b 后与 state.py 位图对齐，便于组合表达多动作。
# ═══════════════════════════════════════════════════════════
ACT_NONE         = 0
ACT_INHIBIT_UP   = state.ACT_INHIBIT_UP   # 0x01
ACT_UNLOAD       = state.ACT_UNLOAD       # 0x02
ACT_COND_FAN_MAX = state.ACT_COND_FAN_MAX # 0x04
ACT_EXV_REDUCE   = state.ACT_EXV_REDUCE   # 0x08
ACT_NORMAL_STOP  = state.ACT_NORMAL_STOP  # 0x10
ACT_SAFE_STOP    = state.ACT_SAFE_STOP    # 0x20


# ═══════════════════════════════════════════════════════════
# 规则定义（tuple，不可变）
# ═══════════════════════════════════════════════════════════
# 结构：(name, bit, delay_key, action, latch, armed_modes, cond_fn, clear_fn, desc)
#   - latch: True=需手动复位；False=条件消失自动清除
#   - armed_modes: 哪些 sys_mode 下武装（空列表=全模式）
#   - cond_fn(mode, now_ms) -> bool: 条件成立?
#   - clear_fn(mode, now_ms) -> bool: 计时器清零条件（条件消失 或 不武装 或 已锁存）

def _hp_cond(mode, now_ms):
    """高压保护：排气压力 > HP_MAX 且 quality OK"""
    idx = state.IDX_DISCHARGE_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["HP_MAX"]

def _hp_clear(mode, now_ms):
    """高压恢复：quality 非 OK 或 值降下来"""
    idx = state.IDX_DISCHARGE_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return True  # 质量坏 → 条件消失（BAD 规则另管）
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    # 有迟滞可在此加，当前规约只需 <= HP_MAX 即恢复
    return state.sensor_checked[idx] <= state.protect_params["HP_MAX"]


def _discharge_psi_bad_cond(mode, now_ms):
    """排气压力传感器 BAD"""
    idx = state.IDX_DISCHARGE_PSI
    return state.sensor_quality[idx] == state.Q_BAD

def _discharge_psi_bad_clear(mode, now_ms):
    """排气压力传感器恢复 Q_OK"""
    idx = state.IDX_DISCHARGE_PSI
    return state.sensor_quality[idx] == state.Q_OK


def _lp_cond(mode, now_ms):
    """低压保护（示值）：吸气压力 < LP_MIN 且 quality OK；仅 RUNNING 武装"""
    if mode != state.MODE_RUNNING:
        return False  # STARTUP 不判定
    idx = state.IDX_SUCTION_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] < state.protect_params["LP_MIN"]

def _lp_clear(mode, now_ms):
    """低压恢复：回到 LP_MIN + LP_HYST 或 quality 非 OK"""
    if mode != state.MODE_RUNNING:
        return True  # 不武装时计时器清零
    idx = state.IDX_SUCTION_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    thresh = state.protect_params["LP_MIN"] + state.protect_params["LP_HYST"]
    return state.sensor_checked[idx] >= thresh


def _suction_psi_bad_cond(mode, now_ms):
    """吸气压力传感器 BAD；仅 RUNNING 武装"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_SUCTION_PSI
    return state.sensor_quality[idx] == state.Q_BAD

def _suction_psi_bad_clear(mode, now_ms):
    """吸气压力传感器恢复 Q_OK"""
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_SUCTION_PSI
    return state.sensor_quality[idx] == state.Q_OK


def _lsh_cond(mode, now_ms):
    """低过热度：SH < SH_MIN 且 quality OK；仅 RUNNING 武装（STARTUP 不投 PID）"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_SUPERHEAT
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] < state.protect_params["SH_MIN"]

def _lsh_clear(mode, now_ms):
    """低过热度恢复：SH 回升或 quality 非 OK"""
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_SUPERHEAT
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] >= state.protect_params["SH_MIN"]


def _oc_cond(mode, now_ms):
    """过流：电流 > I_MAX 且 quality OK"""
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["I_MAX"]

def _oc_clear(mode, now_ms):
    """过流恢复：电流降下或 quality 非 OK"""
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["I_MAX"]


def _current_bad_cond(mode, now_ms):
    """电流传感器 BAD"""
    idx = state.IDX_CURRENT
    return state.sensor_quality[idx] == state.Q_BAD

def _current_bad_clear(mode, now_ms):
    """电流传感器恢复 Q_OK"""
    idx = state.IDX_CURRENT
    return state.sensor_quality[idx] == state.Q_OK


def _link_loss_cond(mode, now_ms):
    """Driver 链路断：link_ok 连续假；仅使能时检查"""
    if not state.protect_params["LINK_CHECK_ENABLE"]:
        return False  # Step4 前不检查
    return not state.driver_link_ok

def _link_loss_clear(mode, now_ms):
    """链路恢复：link_ok 为真"""
    if not state.protect_params["LINK_CHECK_ENABLE"]:
        return True
    return state.driver_link_ok


def _exv_fault_cond(mode, now_ms):
    """
    EXV 硬件故障事件（EXV/EXV.py:check_health() 及 EXV/Valve_CRTL.py 上报）

    数据流（ARCHITECTURE_PLAN.md §14.3-2 单一写者原则）：
      EXV 层检测到 TMC2209 通信失败 / homing 失败 / 驱动器故障
      → 写 state.state_data[ST_VALVE_FAULT]=1（事件位）
      → 本 cond_fn 消费事件位
      → supervisor 走延时表 → EXV_FAULT_DELAY_S 到点 → 锁存 error_reg bit0
      → 手动复位是唯一清除入口（§8.3.6）

    武装范围：全模式。SAFE_STOP 期间 EXV 空转时仍可能上报事件（例如
    homing 未完成即断电前 UART 通讯错），此时也应走延时锁存，不设武装豁免。
    """
    return state.state_data[state.ST_VALVE_FAULT] != 0

def _exv_fault_clear(mode, now_ms):
    """EXV 事件消失：ST_VALVE_FAULT 复位为 0"""
    return state.state_data[state.ST_VALVE_FAULT] == 0


def _sensor_stale_cond(mode, now_ms):
    """全通道超龄（Sensor 协程心跳丢失）；仅 STARTUP/RUNNING 武装"""
    if mode not in (state.MODE_STARTUP, state.MODE_RUNNING):
        return False
    # 取全部 10 通道最小年龄；若最小年龄 > 阈值 → 全死
    min_age = min(state.sensor_age_ms(i) for i in range(10))
    return min_age > (state.protect_params["SENSOR_STALE_DELAY_S"] * 1000)

def _sensor_stale_clear(mode, now_ms):
    """心跳恢复：至少一个通道新鲜"""
    if mode not in (state.MODE_STARTUP, state.MODE_RUNNING):
        return True
    min_age = min(state.sensor_age_ms(i) for i in range(10))
    return min_age <= (state.protect_params["SENSOR_STALE_DELAY_S"] * 1000)


# ── Step5a 新增：设备故障与流量保护 ─────────────────────────
def _compressor_fault_cond(mode, now_ms):
    """
    压缩机故障：消费 Compressor 模块设置的 ST_COMPRESSOR_FAULT 事件位。

    数据流：
      Compressor 模块检测 ESC0 rpm 异常（使能且有目标转速时 rpm==999 或过低）
      → 写 state.state_data[ST_COMPRESSOR_FAULT] = 1
      → 本 cond_fn 消费事件位
      → supervisor 走延时槽 → 锁存 error_reg bit1
    """
    return state.state_data[state.ST_COMPRESSOR_FAULT] != 0

def _compressor_fault_clear(mode, now_ms):
    """压缩机事件消失：ST_COMPRESSOR_FAULT 复位为 0"""
    return state.state_data[state.ST_COMPRESSOR_FAULT] == 0


def _evap_fan_fault_cond(mode, now_ms):
    """蒸发风机故障：消费 Evaporator 模块设置的 ST_EVAP_FAN_FAULT 事件位"""
    return state.state_data[state.ST_EVAP_FAN_FAULT] != 0

def _evap_fan_fault_clear(mode, now_ms):
    return state.state_data[state.ST_EVAP_FAN_FAULT] == 0


def _cond_fan_fault_cond(mode, now_ms):
    """冷凝风机故障：消费 Condenser 模块设置的 ST_COND_FAN_FAULT 事件位"""
    return state.state_data[state.ST_COND_FAN_FAULT] != 0

def _cond_fan_fault_clear(mode, now_ms):
    return state.state_data[state.ST_COND_FAN_FAULT] == 0


def _flow_fault_cond(mode, now_ms):
    """水流开关断开；FLOW_ENABLE=0 时禁用"""
    if not state.protect_params.get("FLOW_ENABLE", 0):
        return False
    return state.state_data[state.ST_FLOW_SWITCH] == 0

def _flow_fault_clear(mode, now_ms):
    if not state.protect_params.get("FLOW_ENABLE", 0):
        return True
    return state.state_data[state.ST_FLOW_SWITCH] != 0


# ── Step5b: L1 限载规则 ─────────────────────────────────────
def _hp_unload_cond(mode, now_ms):
    """高压限载：排气压力 > HP_UNLOAD 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_DISCHARGE_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["HP_UNLOAD"]

def _hp_unload_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_DISCHARGE_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["HP_UNLOAD"]


def _i_limit_cond(mode, now_ms):
    """电流禁升：电流 > I_LIMIT 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["I_LIMIT"]

def _i_limit_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["I_LIMIT"]


def _i_unload_cond(mode, now_ms):
    """电流降容：电流 > I_UNLOAD 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["I_UNLOAD"]

def _i_unload_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_CURRENT
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["I_UNLOAD"]


def _lp_unload_cond(mode, now_ms):
    """低压限载：吸气压力 < LP_UNLOAD 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_SUCTION_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] < state.protect_params["LP_UNLOAD"]

def _lp_unload_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_SUCTION_PSI
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] >= state.protect_params["LP_UNLOAD"]


def _sh_crit_cond(mode, now_ms):
    """临界低过热：SH < SH_CRIT 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_SUPERHEAT
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] < state.protect_params["SH_CRIT"]

def _sh_crit_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_SUPERHEAT
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] >= state.protect_params["SH_CRIT"]


def _td_unload_cond(mode, now_ms):
    """排气温度降容：排气温度 > TD_UNLOAD 且 quality OK；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_DISCHARGE_T
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["TD_UNLOAD"]

def _td_unload_clear(mode, now_ms):
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_DISCHARGE_T
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["TD_UNLOAD"]


# ── Step5b: 排气温度 L2 停机（连续不间断 300s）────────────────
def _td_trip_cond(mode, now_ms):
    """排气温度停机：排气温度 > TD_TRIP 连续；仅 RUNNING"""
    if mode != state.MODE_RUNNING:
        return False
    idx = state.IDX_DISCHARGE_T
    if state.sensor_quality[idx] != state.Q_OK:
        return False
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return False
    return state.sensor_checked[idx] > state.protect_params["TD_TRIP"]

def _td_trip_clear(mode, now_ms):
    """排温降低后条件消失（supervisor 负责连续计时器重置）"""
    if mode != state.MODE_RUNNING:
        return True
    idx = state.IDX_DISCHARGE_T
    if state.sensor_quality[idx] != state.Q_OK:
        return True
    if state.sensor_age_ms(idx) > state.protect_params["AGE_LIMIT_MS"]:
        return True
    return state.sensor_checked[idx] <= state.protect_params["TD_TRIP"]


# ── Step5b: Modbus 通讯断 ───────────────────────────────────
def _modbus_comm_fault_cond(mode, now_ms):
    """无有效 Modbus 帧超 MODBUS_TIMEOUT_S；全模式武装"""
    timeout_ms = state.protect_params["MODBUS_TIMEOUT_S"] * 1000
    # last_modbus_frame_ms=0 表示尚未收到过帧，不判故障
    if state.last_modbus_frame_ms == 0:
        return False
    return time.ticks_diff(now_ms, state.last_modbus_frame_ms) > timeout_ms

def _modbus_comm_fault_clear(mode, now_ms):
    if state.last_modbus_frame_ms == 0:
        return True
    timeout_ms = state.protect_params["MODBUS_TIMEOUT_S"] * 1000
    return time.ticks_diff(now_ms, state.last_modbus_frame_ms) <= timeout_ms


# ═══════════════════════════════════════════════════════════
# 规则表（RULES 为全局只读元组，supervisor 遍历）
# ═══════════════════════════════════════════════════════════
RULES = (
    # (name, bit, delay_key, action, latch, armed_modes, cond_fn, clear_fn, desc)
    
    ("HIGH_PRESSURE_FAULT", 
     state.ERROR_BITS["HIGH_PRESSURE_FAULT"],
     "HP_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _hp_cond, _hp_clear,
     "高压保护（排气压力超限）"),

    ("DISCHARGE_PSI_FAULT",
     state.ERROR_BITS["DISCHARGE_PSI_FAULT"],
     "DISCHARGE_PSI_BAD_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _discharge_psi_bad_cond, _discharge_psi_bad_clear,
     "排气压力传感器 BAD"),

    ("LOW_PRESSURE_FAULT",
     state.ERROR_BITS["LOW_PRESSURE_FAULT"],
     "LP_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _lp_cond, _lp_clear,
     "低压保护（吸气压力过低，仅 RUNNING）"),

    ("SUCTION_PSI_FAULT",
     state.ERROR_BITS["SUCTION_PSI_FAULT"],
     "SUCTION_PSI_BAD_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _suction_psi_bad_cond, _suction_psi_bad_clear,
     "吸气压力传感器 BAD（仅 RUNNING）"),

    ("LOW_SUPERHEAT_FAULT",
     state.ERROR_BITS["LOW_SUPERHEAT_FAULT"],
     "LSH_DELAY_S",
     ACT_EXV_REDUCE, False, [],  # 不锁存，条件消失自动清除
     _lsh_cond, _lsh_clear,
     "低过热度（液击风险，仅 RUNNING）"),

    ("OVERCURRENT_FAULT",
     state.ERROR_BITS["OVERCURRENT_FAULT"],
     "OC_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _oc_cond, _oc_clear,
     "过流保护"),

    # 电流传感器 BAD：latch 取决于 CURRENT_BAD_ACTION
    ("CURRENT_SENSOR_FAULT",
     state.ERROR_BITS["CURRENT_SENSOR_FAULT"],
     "CURRENT_BAD_DELAY_S",
     ACT_NONE,  # 实际动作由 supervisor 根据 action 参数决定
     None,      # latch 标志占位，supervisor 动态查 CURRENT_BAD_ACTION
     [],
     _current_bad_cond, _current_bad_clear,
     "电流传感器 BAD（可配：0=仅告警 1=停机）"),

    # Driver 链路断复用通讯位外独立软标志，见 state.py 注释
    # supervisor 维护 driver_link_latched，不占 error_reg 位
    ("DRIVER_LINK_LOSS",
     None,  # 无 error_reg 位，supervisor 自己记
     "LINK_LOSS_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _link_loss_cond, _link_loss_clear,
     "Driver 链路断（Step4）"),

    # EXV 硬件故障（消费 ST_VALVE_FAULT 事件位，Step3 架构修复）
    ("EXV_FAULT",
     state.ERROR_BITS["EXV_FAULT"],
     "EXV_FAULT_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _exv_fault_cond, _exv_fault_clear,
     "EXV 归零/驱动器故障（消费 ST_VALVE_FAULT 事件）"),

    ("SENSOR_STALE_FAULT",
     state.ERROR_BITS["SENSOR_STALE_FAULT"],
     "SENSOR_STALE_DELAY_S",
     ACT_SAFE_STOP, True, [],
     _sensor_stale_cond, _sensor_stale_clear,
     "传感器全通道超龄 / Sensor 协程心跳丢失"),

    # Step5a 新增：设备故障 / 流量保护（延时槽编号结尾）
    ("COMPRESSOR_FAULT",
     state.ERROR_BITS["COMPRESSOR_FAULT"],
     "COMPRESSOR_FAULT_DELAY_SLOT",
     ACT_SAFE_STOP, True, [],
     _compressor_fault_cond, _compressor_fault_clear,
     "压缩机反馈转速异常（ESC0 无响应/保护）"),

    ("EVAP_FAN_FAULT",
     state.ERROR_BITS["EVAP_FAN_FAULT"],
     "EVAP_FAN_FAULT_DELAY_SLOT",
     ACT_SAFE_STOP, True, [],
     _evap_fan_fault_cond, _evap_fan_fault_clear,
     "蒸发风机反馈转速异常（ESC1 无响应/堵转）"),

    ("COND_FAN_FAULT",
     state.ERROR_BITS["COND_FAN_FAULT"],
     "COND_FAN_FAULT_DELAY_SLOT",
     ACT_SAFE_STOP, True, [],
     _cond_fan_fault_cond, _cond_fan_fault_clear,
     "冷凝风机反馈转速异常（ESC2 无响应/堵转）"),

    ("FLOW_FAULT",
     state.ERROR_BITS["FLOW_FAULT"],
     "FLOW_FAULT_DELAY_SLOT",
     ACT_SAFE_STOP, True, [],
     _flow_fault_cond, _flow_fault_clear,
     "水流开关断开（FLOW_ENABLE=1 时生效）"),

    # Step5b: L1 限载规则（不锁存 error_reg，只通过 limit_actions 表达）
    ("HIGH_PRESSURE_UNLOAD",
     None,  # L1 不置 error_reg
     "L1_LIMIT_DELAY_SLOT",
     ACT_UNLOAD | ACT_COND_FAN_MAX, False, [],
     _hp_unload_cond, _hp_unload_clear,
     "高压限载（排气压力 > HP_UNLOAD）"),

    ("CURRENT_LIMIT",
     None,
     "L1_LIMIT_DELAY_SLOT",
     ACT_INHIBIT_UP, False, [],
     _i_limit_cond, _i_limit_clear,
     "电流禁升（电流 > I_LIMIT）"),

    ("CURRENT_UNLOAD",
     None,
     "L1_LIMIT_DELAY_SLOT",
     ACT_UNLOAD, False, [],
     _i_unload_cond, _i_unload_clear,
     "电流降容（电流 > I_UNLOAD）"),

    ("LOW_PRESSURE_UNLOAD",
     None,
     "L1_LIMIT_DELAY_SLOT",
     ACT_UNLOAD, False, [],
     _lp_unload_cond, _lp_unload_clear,
     "低压限载（吸气压力 < LP_UNLOAD）"),

    ("CRITICAL_LOW_SUPERHEAT",
     None,
     "L1_LIMIT_DELAY_SLOT",
     ACT_UNLOAD | ACT_EXV_REDUCE, False, [],
     _sh_crit_cond, _sh_crit_clear,
     "临界低过热（SH < SH_CRIT）"),

    ("DISCHARGE_TEMP_UNLOAD",
     None,
     "L1_LIMIT_DELAY_SLOT",
     ACT_UNLOAD, False, [],
     _td_unload_cond, _td_unload_clear,
     "排气温度降容（排气温度 > TD_UNLOAD）"),

    # Step5b: L2 排气温度停机（连续 300s）
    ("DISCHARGE_TEMP_TRIP",
     state.ERROR_BITS["HIGH_DISCHARGE_TEMP"],
     "TD_TRIP_DELAY_SLOT",
     ACT_SAFE_STOP, True, [],
     _td_trip_cond, _td_trip_clear,
     "排气温度停机（排气温度 > TD_TRIP 连续 300s）"),

    # Step5b: Modbus 通讯断 → 正常停机（不锁存 error_reg）
    ("MODBUS_COMM_FAULT",
     None,
     "MODBUS_TIMEOUT_S",
     ACT_NORMAL_STOP, False, [],
     _modbus_comm_fault_cond, _modbus_comm_fault_clear,
     "Modbus 通讯断（延时 60s 正常停机）"),
)


# ═══════════════════════════════════════════════════════════
# 对外接口（供 supervisor 调用）
# ═══════════════════════════════════════════════════════════
def evaluate_all(mode, now_ms):
    """
    评估所有规则，返回 list of (rule_tuple, condition_met, should_clear_timer)
    
    返回：[(rule, cond_met, should_clear), ...]
    - cond_met: 条件是否成立（True=启动/继续计时）
    - should_clear: 是否应清零计时器（条件消失 或 不武装）
    """
    results = []
    for rule in RULES:
        name, bit, delay_key, action, latch, armed_modes, cond_fn, clear_fn, desc = rule
        cond_met = cond_fn(mode, now_ms)
        should_clear = clear_fn(mode, now_ms) if not cond_met else False
        results.append((rule, cond_met, should_clear))
    return results

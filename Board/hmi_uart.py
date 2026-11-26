"""
UART HMI 通信协程（见 docs/PC_HMI_UART_DESIGN.md）。

提供三个协程：
  - hmi_rx()        每 20 ms 非阻塞读取、校验、分派命令
  - hmi_tx()        每秒生成动态快照，发送队列内容（分块 32 字节）
  - hmi_watchdog()  每 100 ms 检查通信超时

依赖：Board.state, Board.params_store, Board.board.HMI_UART,
      Board.hmi_points（自动生成）
"""

import uasyncio as asyncio
import utime as time

import Board.state as state
import Board.params_store as params_store
from Board.board import HMI_UART
from Board.hmi_points import (
    HMI_VERSION, DYNAMIC_COUNT, PARAM_COUNT, LIST_HASH,
    DYNAMIC_SYMBOLS, PARAM_SYMBOLS, POINTS_META, WRITABLE_SYMBOLS, ACTION_SYMBOLS,
)

# ────────────────────────────────────────────────────────────
# 常量（见设计文档 4.2、8）
# ────────────────────────────────────────────────────────────
RX_PERIOD_MS = 20
TX_PERIOD_MS = 1000          # 动态快照周期
WATCHDOG_PERIOD_MS = 100
WATCHDOG_TIMEOUT_MS = 3000   # 3s 无合法命令行则停机
CHUNK_SIZE = 32              # 每次 write 不超过 32 字节
MAX_LINE_LEN = 256
MAX_BATCH_PAIRS = 5

FLOAT_ABS_MAX = 100000000.0
INT_ABS_MAX = 2147483647
UINT32_MAX = 4294967295

# 数值文本长度上限（6.2）
MAX_FLOAT_CHARS = 12
MAX_INT_CHARS = 11
MAX_BITMAP_CHARS = 10

# ────────────────────────────────────────────────────────────
# 预分配缓冲（4.4）
# ────────────────────────────────────────────────────────────
_dyn_buf = bytearray(1024)
_param_buf = bytearray(2304)
_range_buf = bytearray(64)

# 应答槽：4 行 x 64 字节
RESPONSE_SLOT_COUNT = 4
RESPONSE_LINE_SIZE = 64
_response_slot = [bytearray(RESPONSE_LINE_SIZE) for _ in range(RESPONSE_SLOT_COUNT)]
_response_len = [0] * RESPONSE_SLOT_COUNT
_response_head = 0
_response_tail = 0

# 数据槽：2 项；每项可以是 bytearray 或 range 列表迭代器
_data_slot = [None, None]
_data_len = [0, 0]
_data_iter = [None, None]
_data_head = 0
_data_tail = 0

# ────────────────────────────────────────────────────────────
# 运行时状态
# ────────────────────────────────────────────────────────────
_last_hmi_rx_ms = 0
_need_new_snapshot = False
_snapshot_seq = 0

# 日志/统计
_log_buf = []

def _log(msg):
    _log_buf.append(msg)
    if len(_log_buf) > 16:
        _log_buf.pop(0)
    print("[HMI]", msg)


def _now_ms():
    return time.ticks_ms()


def _crc_xor(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


# ────────────────────────────────────────────────────────────
# 手写数字格式化（4.4：不用 f-string/%/format 生成整行）
# ────────────────────────────────────────────────────────────
def _append_int(buf, offset, value):
    """把整数以十进制写入 buf，返回新 offset。"""
    if value < 0:
        buf[offset] = ord('-')
        offset += 1
        value = -value
    if value == 0:
        buf[offset] = ord('0')
        return offset + 1
    # 先写反序
    start = offset
    while value > 0:
        buf[offset] = ord('0') + (value % 10)
        offset += 1
        value //= 10
    # 反转
    end = offset - 1
    while start < end:
        buf[start], buf[end] = buf[end], buf[start]
        start += 1
        end -= 1
    return offset


def _append_float(buf, offset, value):
    """把浮点按两位小数写入 buf，返回新 offset。"""
    if value < 0:
        buf[offset] = ord('-')
        offset += 1
        value = -value
    # 乘 100 四舍五入
    scaled = int(value * 100 + 0.5)
    integer = scaled // 100
    frac = scaled % 100
    offset = _append_int(buf, offset, integer)
    buf[offset] = ord('.')
    offset += 1
    buf[offset] = ord('0') + (frac // 10)
    offset += 1
    buf[offset] = ord('0') + (frac % 10)
    offset += 1
    return offset


# ────────────────────────────────────────────────────────────
# 符号 -> state 映射（附录 C）
# ────────────────────────────────────────────────────────────
_PID_KEY_MAP = {
    "KP": "Kp", "KI": "Ki", "KD": "Kd",
    "SETPOINT": "setpoint", "ERROR_THRESHOLD": "error_threshold",
    "DEADBAND": "deadband", "MAX_DELTA": "max_delta",
    "TAU": "tau", "T_GOAL": "T_goal", "AGGR_MODE": "aggr_mode",
    "THROTTLE_MIN": "throttle_min", "THROTTLE_MAX": "throttle_max",
    "PERIOD_S": "period_s", "RPM_MAX": "rpm_max",
    "THROTTLE_MAX_ADJUST": "throttle_max_adjust",
}


def _pid_key(suffix):
    return _PID_KEY_MAP.get(suffix, suffix)


def _read_param_value(symbol):
    """读取可写参数点位的当前值。"""
    if symbol == "SYSTEM_ENABLE":
        return state.state_data[state.ST_SYSTEM_ENABLE]

    # A.7 手动项
    if symbol in state.manual_params:
        return state.manual_params[symbol]
    if symbol in state.evap_fan_user_params:
        return state.evap_fan_user_params[symbol]

    # protect_params（先判断，避免 SH_MIN 被前缀误匹配）
    if symbol in state.protect_params:
        return state.protect_params[symbol]
    if symbol.startswith("DELAY_SLOT_"):
        try:
            idx = int(symbol.split("_")[-1])
            return state.protect_params["delay_slots"][idx]
        except Exception:
            pass

    # SH_ / CAP_ / COND_ / CSL_ / FSL_
    for prefix, attr in (
        ("SH_", "control_params"),
        ("CAP_", "compressor_control_params"),
        ("COND_", "cond_control_params"),
        ("CSL_", "comp_speed_loop_params"),
        ("FSL_", "fan_speed_loop_params"),
    ):
        if symbol.startswith(prefix):
            suffix = symbol[len(prefix):]
            d = getattr(state, attr)
            key = _pid_key(suffix)
            val = d[key]
            if key == "aggr_mode":
                return {"off": 1, "proportional": 2, "togoal": 3}.get(val, 3)
            return val

    # A.8 ESC 遥测配置
    if symbol in state.esc_telemetry_params:
        return state.esc_telemetry_params[symbol]

    return None


def _write_param_value(symbol, value):
    """写入可写参数点位；返回是否成功。"""
    if symbol == "SYSTEM_ENABLE":
        state.state_data[state.ST_SYSTEM_ENABLE] = 1 if value else 0
        return True

    if symbol in state.manual_params:
        state.manual_params[symbol] = value
        return True
    if symbol in state.evap_fan_user_params:
        state.evap_fan_user_params[symbol] = value
        return True

    if symbol in state.protect_params:
        state.protect_params[symbol] = value
        return True
    if symbol.startswith("DELAY_SLOT_"):
        idx = int(symbol.split("_")[-1])
        state.protect_params["delay_slots"][idx] = value
        return True

    for prefix, attr in (
        ("SH_", "control_params"),
        ("CAP_", "compressor_control_params"),
        ("COND_", "cond_control_params"),
        ("CSL_", "comp_speed_loop_params"),
        ("FSL_", "fan_speed_loop_params"),
    ):
        if symbol.startswith(prefix):
            suffix = symbol[len(prefix):]
            d = getattr(state, attr)
            key = _pid_key(suffix)
            if key == "aggr_mode":
                # 协议 1/2/3 -> state 字符串
                d[key] = {1: "off", 2: "proportional", 3: "togoal"}.get(value, "togoal")
            else:
                d[key] = value
            return True

    # A.8 ESC 遥测配置：写入扁平参数字典并同步回驱动使用的列表
    if symbol in state.esc_telemetry_params:
        state.esc_telemetry_params[symbol] = value
        if hasattr(state, "sync_esc_telemetry_cfg"):
            state.sync_esc_telemetry_cfg()
        return True

    return False


def _read_dynamic_value(symbol):
    """读取动态清单点位的当前值。"""
    # B.1 传感器
    sensor_map = {
        "SUCTION_TEMP": state.IDX_SUCTION_T,
        "DISCHARGE_TEMP": state.IDX_DISCHARGE_T,
        "INLET_TEMP": state.IDX_INLET_T,
        "OUTLET_TEMP": state.IDX_OUTLET_T,
        "LIQUID_TEMP": state.IDX_LIQUID_T,
        "SUCTION_PRESSURE": state.IDX_SUCTION_PSI,
        "DISCHARGE_PRESSURE": state.IDX_DISCHARGE_PSI,
        "SUPERHEAT": state.IDX_SUPERHEAT,
        "SUCTION_ENTHALPY": state.IDX_E_VALUE,
        "CURRENT": state.IDX_CURRENT,
    }
    if symbol in sensor_map:
        return state.sensor_data[sensor_map[symbol]]
    if symbol == "SUBCOOLING":
        return state.subcooling
    if symbol == "LIQUID_ENTHALPY":
        return state.liquid_enthalpy
    if symbol.startswith("SENSOR_QUALITY_"):
        idx = int(symbol.split("_")[-1])
        return state.sensor_quality[idx]
    if symbol == "SENSOR_FAULT_REG":
        return state.sensor_fault_reg
    if symbol == "SENSOR_STALE_FLAG":
        return state.state_data[state.ST_SENSOR_FAULT]

    # B.2 运行状态
    if symbol == "SYS_MODE":
        return state.sys_mode
    if symbol == "STARTUP_STAGE":
        return state.startup_stage
    if symbol == "RUNNING_SUBSTATE":
        return state.running_substate
    if symbol == "LIMIT_ACTIONS":
        return state.limit_actions
    if symbol == "RETRY_LOCKOUT":
        return 1 if state.retry_lockout else 0
    if symbol == "DRIVER_LINK_OK":
        return 1 if state.driver_link_ok else 0

    # B.3 执行器反馈
    if symbol == "COMP_RPM":
        return state.state_data[state.ST_MOTOR_SPEED]
    if symbol == "COMP_THROTTLE_OUT":
        return state.comp_throttle_out
    if symbol == "EVAP_FAN_RPM":
        return state.state_data[state.ST_EVAP_FAN_RPM]
    if symbol == "EVAP_FAN_CMD":
        return state.state_data[state.ST_EVAP_FAN_SPEED_CMD]
    if symbol == "EVAP_FAN_THROTTLE_OUT":
        return state.evap_fan_throttle_out
    if symbol == "COND_FAN_RPM":
        return state.state_data[state.ST_COND_FAN_RPM]
    if symbol == "COND_FAN_CMD":
        return state.state_data[state.ST_COND_FAN_SPEED_CMD]
    if symbol == "COND_FAN_THROTTLE_OUT":
        return state.cond_fan_throttle_out
    if symbol == "EXV_POS":
        return state.state_data[state.ST_VALVE_POS]
    if symbol == "EXV_STEPS":
        return state.state_data[state.ST_VALVE_POS_STEPS]
    if symbol == "EXV_TARGET_PCT":
        return state.exv_target_pct
    if symbol == "EXV_DRIVE_FAULT":
        return state.state_data[state.ST_VALVE_FAULT]
    if symbol == "EVAP_FAN_FAULT_FLAG":
        return state.state_data[state.ST_EVAP_FAN_FAULT]
    if symbol == "COND_FAN_FAULT_FLAG":
        return state.state_data[state.ST_COND_FAN_FAULT]
    if symbol == "COMPRESSOR_FAULT_FLAG":
        return state.state_data[state.ST_COMPRESSOR_FAULT]
    if symbol == "MOTOR_SPEED_MIN":
        return state.state_data[state.ST_MOTOR_SPEED_MIN]
    if symbol == "MOTOR_SPEED_MAX":
        return state.state_data[state.ST_MOTOR_SPEED_MAX]
    if symbol == "FAN_SPEED_MIN":
        return state.state_data[state.ST_FAN_SPEED_MIN]
    if symbol == "FAN_SPEED_MAX":
        return state.state_data[state.ST_FAN_SPEED_MAX]
    if symbol == "FLOW_SWITCH":
        return state.state_data[state.ST_FLOW_SWITCH]

    # B.4 ESC 遥测
    esc_map = {
        "ESC0_RPM": state.ST_ESC0_RPM, "ESC0_TEMP": state.ST_ESC0_TEMP,
        "ESC0_VOLTAGE": state.ST_ESC0_VOLTAGE, "ESC0_CURRENT": state.ST_ESC0_CURRENT,
        "ESC1_RPM": state.ST_ESC1_RPM, "ESC1_TEMP": state.ST_ESC1_TEMP,
        "ESC1_VOLTAGE": state.ST_ESC1_VOLTAGE, "ESC1_CURRENT": state.ST_ESC1_CURRENT,
        "ESC2_RPM": state.ST_ESC2_RPM, "ESC2_TEMP": state.ST_ESC2_TEMP,
        "ESC2_VOLTAGE": state.ST_ESC2_VOLTAGE, "ESC2_CURRENT": state.ST_ESC2_CURRENT,
        "ESC3_RPM": state.ST_ESC3_RPM, "ESC3_TEMP": state.ST_ESC3_TEMP,
        "ESC3_VOLTAGE": state.ST_ESC3_VOLTAGE, "ESC3_CURRENT": state.ST_ESC3_CURRENT,
    }
    if symbol in esc_map:
        return state.state_data[esc_map[symbol]]

    # B.5 报警
    if symbol == "ERROR_REG":
        return state.error_reg

    # B.6 联锁
    interlock_key = {
        "COMP_ENABLE": "comp_enable",
        "COMP_THROTTLE_CMD": "comp_throttle_cmd",
        "EVAP_FAN_ENABLE": "evap_fan_enable",
        "EVAP_FAN_THROTTLE_CMD": "evap_fan_throttle_cmd",
        "COND_FAN_ENABLE": "cond_fan_enable",
        "COND_FAN_THROTTLE_CMD": "cond_fan_throttle_cmd",
        "EXV_ENABLE": "exv_enable",
    }
    if symbol in interlock_key:
        v = state.supervisor_interlocks[interlock_key[symbol]]
        return 1 if v else 0

    # B.7 SYSTEM_ENABLE
    if symbol == "SYSTEM_ENABLE":
        return state.state_data[state.ST_SYSTEM_ENABLE]

    return None


# ────────────────────────────────────────────────────────────
# 快照格式化
# ────────────────────────────────────────────────────────────
def _format_snapshot(buf, offset, symbols, reader):
    """用 reader 读取 symbols 并格式化进 buf；返回新 offset。"""
    first = True
    for sym in symbols:
        if not first:
            buf[offset] = ord(' ')
            offset += 1
        first = False
        val = reader(sym)
        ptype = POINTS_META.get(sym, {}).get("type", "i")
        if val is None:
            val = 999 if sym in (
                "SUCTION_TEMP", "DISCHARGE_TEMP", "INLET_TEMP", "OUTLET_TEMP", "LIQUID_TEMP",
                "SUCTION_PRESSURE", "DISCHARGE_PRESSURE", "SUPERHEAT", "SUCTION_ENTHALPY",
                "CURRENT", "SUBCOOLING", "LIQUID_ENTHALPY",
            ) else 0
        if ptype == "f":
            offset = _append_float(buf, offset, float(val))
        else:
            # 位图按无符号 32 位输出
            if ptype == "b" and val < 0:
                val += 0x100000000
            offset = _append_int(buf, offset, int(val))
    buf[offset] = ord('\n')
    return offset + 1


def _build_dynamic_snapshot(seq):
    """生成 S <seq> ... 行，返回实际长度。"""
    global _dyn_buf
    # 清空并写入前缀
    offset = 0
    _dyn_buf[offset] = ord('S')
    offset += 1
    _dyn_buf[offset] = ord(' ')
    offset += 1
    offset = _append_int(_dyn_buf, offset, seq)
    _dyn_buf[offset] = ord(' ')
    offset += 1
    offset = _format_snapshot(_dyn_buf, offset, DYNAMIC_SYMBOLS, _read_dynamic_value)
    return offset


def _build_param_snapshot():
    global _param_buf
    offset = 0
    _param_buf[offset] = ord('Q')
    offset += 1
    _param_buf[offset] = ord(' ')
    offset += 1
    # Q 快照必须读取参数映射，不能误用动态点位读取函数。
    offset = _format_snapshot(_param_buf, offset, PARAM_SYMBOLS, _read_param_value)
    return offset


def _build_range_line(symbol, min_val, max_val):
    """生成 R <symbol> <min> <max> 行，返回 bytes。"""
    global _range_buf
    ptype = POINTS_META.get(symbol, {}).get("type", "i")
    offset = 0
    _range_buf[offset] = ord('R')
    offset += 1
    _range_buf[offset] = ord(' ')
    offset += 1
    for ch in symbol:
        _range_buf[offset] = ord(ch)
        offset += 1
    _range_buf[offset] = ord(' ')
    offset += 1
    if min_val is None:
        _range_buf[offset] = ord('-')
        offset += 1
    else:
        if ptype == "f":
            offset = _append_float(_range_buf, offset, float(min_val))
        else:
            offset = _append_int(_range_buf, offset, int(min_val))
    _range_buf[offset] = ord(' ')
    offset += 1
    if max_val is None:
        _range_buf[offset] = ord('-')
        offset += 1
    else:
        if ptype == "f":
            offset = _append_float(_range_buf, offset, float(max_val))
        else:
            offset = _append_int(_range_buf, offset, int(max_val))
    _range_buf[offset] = ord('\n')
    return bytes(_range_buf[:offset + 1])


# ────────────────────────────────────────────────────────────
# 发送队列（3.1）
# ────────────────────────────────────────────────────────────
def _enqueue_response(text: str):
    """把文本行加入应答槽；满则丢弃并记日志。"""
    global _response_tail
    # 检查是否还有空位
    next_tail = (_response_tail + 1) % RESPONSE_SLOT_COUNT
    if next_tail == _response_head:
        _log("应答槽满，丢弃: " + text)
        return
    buf = _response_slot[_response_tail]
    data = text.encode("utf-8")
    ln = min(len(data), RESPONSE_LINE_SIZE - 1)
    buf[:ln] = data[:ln]
    buf[ln] = ord('\n')
    _response_len[_response_tail] = ln + 1
    _response_tail = next_tail


def _dequeue_response():
    """返回 (buf, len) 或 None。"""
    global _response_head
    if _response_head == _response_tail:
        return None
    idx = _response_head
    _response_head = (_response_head + 1) % RESPONSE_SLOT_COUNT
    return _response_slot[idx], _response_len[idx]


def _enqueue_data(data):
    """把数据任务加入数据槽。data: bytearray 或 (iter_type, iter_obj)。"""
    global _data_tail
    next_tail = (_data_tail + 1) % 2
    if next_tail == _data_head:
        # 数据槽满：若待发位是 S 则丢弃让位
        if _data_slot[_data_tail] is not None and isinstance(_data_slot[_data_tail], bytearray):
            _data_slot[_data_tail] = None
            _data_len[_data_tail] = 0
            _log("数据槽满，丢弃旧 S")
        else:
            return False
    _data_slot[_data_tail] = data
    _data_len[_data_tail] = len(data) if isinstance(data, bytearray) else 0
    _data_iter[_data_tail] = None
    _data_tail = next_tail
    return True


def _current_data_slot():
    if _data_head == _data_tail:
        return None
    return _data_head


def _advance_data_slot():
    global _data_head
    _data_slot[_data_head] = None
    _data_len[_data_head] = 0
    _data_iter[_data_head] = None
    _data_head = (_data_head + 1) % 2


# ────────────────────────────────────────────────────────────
# 命令解析与校验
# ────────────────────────────────────────────────────────────
def _parse_number(text: str):
    """解析整数或浮点；失败返回 None。"""
    try:
        if "." in text:
            return float(text)
        return int(text)
    except Exception:
        return None


def _validate_value(symbol, text, check_user_range=True):
    """校验单个数值文本；成功返回 (True, value)，失败返回 (False, error_code)。"""
    meta = POINTS_META.get(symbol)
    if meta is None:
        return False, "NAME"
    ptype = meta.get("type", "i")

    # 非有限数检查
    if text.lower() in ("nan", "inf", "-inf", "+inf"):
        return False, "FORMAT"

    # 整数点位收到小数
    if ptype in ("i", "e", "b", "s") and ("." in text or "e" in text.lower()):
        return False, "FORMAT"

    val = _parse_number(text)
    if val is None:
        return False, "FORMAT"

    # 表示上限
    if ptype == "f":
        if abs(val) >= FLOAT_ABS_MAX:
            return False, "FORMAT"
    elif ptype == "b":
        if val < 0 or val > UINT32_MAX:
            return False, "FORMAT"
    else:
        if abs(val) > INT_ABS_MAX:
            return False, "FORMAT"

    # 用户范围
    if check_user_range:
        rmin, rmax = params_store.get_range(symbol)
        if rmin is not None and val < rmin:
            return False, "RANGE"
        if rmax is not None and val > rmax:
            return False, "RANGE"

    # 精度：浮点最多两位小数，超出返回 PRECISION（7.4），不静默四舍五入
    if ptype == "f":
        if "." in text:
            frac = text.split(".", 1)[1]
            if len(frac) > 2:
                return False, "PRECISION"
        val = round(val, 2)
    else:
        val = int(val)

    return True, val


# 关系约束（6.3）
_CONSTRAINT_GROUPS = [
    ("HP_TARGET", "HP_WARN", "HP_UNLOAD", "HP_MAX"),
    ("LP_MIN", "LP_UNLOAD", "LP_WARN"),
    ("I_WARN", "I_LIMIT", "I_UNLOAD", "I_MAX"),
    ("TD_WARN", "TD_UNLOAD", "TD_TRIP"),
    ("SH_CRIT", "SH_MIN"),
    ("CAP_THROTTLE_MIN", "CAP_THROTTLE_MAX"),
    ("COND_THROTTLE_MIN", "COND_THROTTLE_MAX"),
]


def _check_constraints(new_values):
    """new_values: {symbol: new_value}。只检查涉及的约束组。"""
    for group in _CONSTRAINT_GROUPS:
        vals = []
        for sym in group:
            if sym in new_values:
                vals.append(new_values[sym])
            else:
                v = _read_param_value(sym)
                if v is None:
                    v = 0
                vals.append(v)
        for i in range(len(vals) - 1):
            if vals[i] >= vals[i + 1]:
                return False, group[i + 1]

    # 槽号 0-7
    for sym in new_values:
        if sym.endswith("_DELAY_SLOT"):
            if not (0 <= new_values[sym] <= 7):
                return False, sym

    # STARTUP_ADVANCE_MODE 0/1
    if "STARTUP_ADVANCE_MODE" in new_values:
        if new_values["STARTUP_ADVANCE_MODE"] not in (0, 1):
            return False, "STARTUP_ADVANCE_MODE"

    # AGGR_MODE 1/2/3
    for sym in new_values:
        if sym.endswith("_AGGR_MODE"):
            if new_values[sym] not in (1, 2, 3):
                return False, sym

    # 开关量 0/1
    switch_syms = {
        "SYSTEM_ENABLE", "MANUAL_MODE_ENABLE", "LINK_CHECK_ENABLE",
        "COMP_SPEED_LOOP_ENABLE", "COND_FAN_SPEED_LOOP_ENABLE", "EVAP_FAN_SPEED_LOOP_ENABLE",
        "EVAP_FAN_THERMO_OFF_KEEP", "FLOW_ENABLE",
        "CURRENT_BAD_ACTION",
    }
    for sym in new_values:
        if sym in switch_syms:
            if new_values[sym] not in (0, 1):
                return False, sym

    return True, None


# 有副作用的写入（6.3）
_SIDE_EFFECTS = {
    "HP_TARGET": ("COND_SETPOINT", lambda v: v),
    "CSL_RPM_MAX": ("MOTOR_SPEED_MAX", lambda v: int(v)),
    "FSL_RPM_MAX": ("FAN_SPEED_MAX", lambda v: int(v)),
}


def _do_write(symbol, value):
    """执行单点写入（含副作用）；返回是否成功。"""
    if not _write_param_value(symbol, value):
        return False
    if symbol in _SIDE_EFFECTS:
        target_sym, transform = _SIDE_EFFECTS[symbol]
        target_val = transform(value)
        if target_sym in POINTS_META and POINTS_META[target_sym]["list"] == "S":
            # 动态点位：直接写 state_data
            if target_sym == "MOTOR_SPEED_MAX":
                state.state_data[state.ST_MOTOR_SPEED_MAX] = target_val
            elif target_sym == "FAN_SPEED_MAX":
                state.state_data[state.ST_FAN_SPEED_MAX] = target_val
        elif target_sym in POINTS_META and POINTS_META[target_sym]["list"] == "Q":
            _write_param_value(target_sym, target_val)
    return True


# ────────────────────────────────────────────────────────────
# 命令分派
# ────────────────────────────────────────────────────────────
def _handle_stop_line(line):
    """处理 W SYSTEM_ENABLE 0*XX 插队命令；返回 True 表示已处理。"""
    # 必须带校验
    if "*" not in line:
        return False
    star_idx = line.index("*")
    body = line[:star_idx]
    crc_str = line[star_idx + 1:]
    if body != "W SYSTEM_ENABLE 0":
        return False
    try:
        expected = int(crc_str, 16)
    except Exception:
        return False
    if _crc_xor(body.encode("utf-8")) != expected:
        return False
    # 执行
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    _enqueue_response("OK")
    return True


def _handle_command(line: str):
    """解析并执行一行命令；把应答放入应答槽。"""
    global _last_hmi_rx_ms

    # 超长行
    if len(line) > MAX_LINE_LEN:
        _enqueue_response("E FORMAT")
        return

    # 停机命令优先（7.1）：在提取 CRC 之前先识别
    # W SYSTEM_ENABLE 0*XX
    if line.startswith("W SYSTEM_ENABLE 0"):
        if _handle_stop_line(line):
            _last_hmi_rx_ms = _now_ms()
            return

    # 先区分 R *（列出范围，无校验）与行尾 *XX（校验）。
    # 不能把 R * 中作为命令参数的星号误识别为 CRC 分隔符。
    stripped = line.strip()
    if stripped == "R *":
        parts = ["R", "*"]
        cmd = "R"
    else:
        raw_parts = stripped.split()
        if not raw_parts:
            return
        cmd = raw_parts[0]
        needs_crc = cmd in ("W", "A") or (cmd == "R" and len(raw_parts) >= 3)

        # 有副作用的命令必须带校验；无副作用命令不带校验。
        if needs_crc and "*" not in stripped:
            _enqueue_response("E CRC")
            return

        if "*" in stripped:
            if not needs_crc:
                _enqueue_response("E FORMAT")
                return
            star_idx = stripped.rfind("*")
            body = stripped[:star_idx]
            crc_str = stripped[star_idx + 1:]
            if len(crc_str) != 2:
                _enqueue_response("E CRC")
                return
            try:
                expected = int(crc_str, 16)
            except Exception:
                _enqueue_response("E CRC")
                return
            if _crc_xor(body.encode("utf-8")) != expected:
                _enqueue_response("E CRC")
                return
            parts = body.split()
        else:
            parts = raw_parts

    # 命令分派
    if not parts:
        return
    cmd = parts[0]

    if cmd == "H":
        _enqueue_response("H {} {} {} {}".format(HMI_VERSION, DYNAMIC_COUNT, PARAM_COUNT, LIST_HASH))
        _last_hmi_rx_ms = _now_ms()

    elif cmd == "P":
        _last_hmi_rx_ms = _now_ms()

    elif cmd == "G":
        global _need_new_snapshot
        _need_new_snapshot = True
        _last_hmi_rx_ms = _now_ms()

    elif cmd == "Q":
        data = _param_buf[:_build_param_snapshot()]
        if _enqueue_data(data):
            _last_hmi_rx_ms = _now_ms()
        else:
            _enqueue_response("E BUSY")

    elif cmd == "W":
        _handle_write(parts)
        _last_hmi_rx_ms = _now_ms()

    elif cmd == "A":
        _handle_action(parts)
        _last_hmi_rx_ms = _now_ms()

    elif cmd == "R":
        _handle_range(parts)
        _last_hmi_rx_ms = _now_ms()

    else:
        _enqueue_response("E FORMAT")


def _handle_write(parts):
    """处理 W 命令（含批量）。"""
    if len(parts) < 3 or (len(parts) - 1) % 2 != 0:
        _enqueue_response("E FORMAT")
        return
    pairs_text = parts[1:]
    n = len(pairs_text) // 2
    if n > MAX_BATCH_PAIRS:
        _enqueue_response("E FORMAT")
        return

    symbols = []
    values_text = []
    for i in range(n):
        sym = pairs_text[2 * i]
        val_text = pairs_text[2 * i + 1]
        if sym in symbols:
            _enqueue_response("E FORMAT")
            return
        symbols.append(sym)
        values_text.append(val_text)

    # 校验每个符号存在且可写
    for sym in symbols:
        if sym not in POINTS_META:
            _enqueue_response("E NAME")
            return
        if POINTS_META[sym]["rw"] != "W":
            _enqueue_response("E READONLY")
            return
        if sym == "FLOW_ENABLE":
            _enqueue_response("E READONLY")
            return

    # 校验每个数值
    new_values = {}
    for sym, text in zip(symbols, values_text):
        ok, val = _validate_value(sym, text)
        if not ok:
            _enqueue_response("E {} {}".format(val, sym))
            return
        new_values[sym] = val

    # 关系约束
    ok, bad_sym = _check_constraints(new_values)
    if not ok:
        _enqueue_response("E RANGE {}".format(bad_sym))
        return

    # 记录原值并原子写入
    originals = {}
    written = []
    try:
        for sym in symbols:
            originals[sym] = _read_param_value(sym)
            if not _do_write(sym, new_values[sym]):
                raise RuntimeError("write failed")
            written.append(sym)
    except Exception as e:
        # 回滚
        for sym in written:
            _do_write(sym, originals[sym])
        _enqueue_response("E INTERNAL")
        _log("原子写入失败: " + str(e))
        return

    # 持久化：只有写到的符号里存在持久化点位才标脏（SYSTEM_ENABLE、A.7 手动项不持久化）
    if any(POINTS_META[sym].get("persistent") == "Y" for sym in symbols):
        params_store.mark_dirty()
    _enqueue_response("OK")


def _handle_action(parts):
    """处理 A 命令。"""
    if len(parts) != 2:
        _enqueue_response("E ACTION")
        return
    sym = parts[1]
    if sym not in ACTION_SYMBOLS:
        _enqueue_response("E ACTION")
        return
    if sym == "FAULT_RESET":
        state.fault_reset_request = True
        _enqueue_response("OK")


def _handle_range(parts):
    """处理 R 命令（查询/设定/清除/列出全部）。"""
    if len(parts) < 2:
        _enqueue_response("E FORMAT")
        return
    symbol = parts[1]

    if symbol == "*":
        # 列出全部已设定范围
        ranges = params_store.get_all_ranges()
        items = list(ranges.items())
        if not _enqueue_data(("ranges", items)):
            _enqueue_response("E BUSY")
        return

    if len(parts) == 2:
        # 查询单点范围
        if symbol not in POINTS_META:
            _enqueue_response("E NAME")
            return
        rmin, rmax = params_store.get_range(symbol)
        if rmin is None:
            _enqueue_response("R {} - -".format(symbol))
        else:
            _enqueue_response("R {} {} {}".format(symbol, rmin, rmax))
        return

    if len(parts) == 3 and parts[2] == "D":
        # 清除范围
        if symbol not in POINTS_META or POINTS_META[symbol]["rw"] != "W":
            _enqueue_response("E READONLY")
            return
        params_store.clear_range(symbol)
        _enqueue_response("OK")
        return

    if len(parts) == 4:
        # 设定范围
        if symbol not in POINTS_META or POINTS_META[symbol]["rw"] != "W":
            _enqueue_response("E READONLY")
            return
        # 范围值只检查类型与表示上限，不检查旧用户范围
        ok_min, min_val = _validate_value(symbol, parts[2], check_user_range=False)
        ok_max, max_val = _validate_value(symbol, parts[3], check_user_range=False)
        if not ok_min or not ok_max:
            code = min_val if not ok_min else max_val
            _enqueue_response("E {} {}".format(code, symbol))
            return
        if min_val > max_val:
            _enqueue_response("E RANGE {}".format(symbol))
            return
        params_store.set_range(symbol, min_val, max_val)
        _enqueue_response("OK")
        return

    _enqueue_response("E FORMAT")


# ────────────────────────────────────────────────────────────
# 协程
# ────────────────────────────────────────────────────────────
async def hmi_rx():
    """接收协程（见 PC_HMI_UART_DESIGN.md 第 3 节、4.2、7.2）。"""
    rx_buf = bytearray()
    while True:
        try:
            n = HMI_UART.any()
            if n:
                data = HMI_UART.read(n)
                if data:
                    rx_buf.extend(data)
            # 按 LF 切行
            while True:
                idx = -1
                for i, b in enumerate(rx_buf):
                    if b == 10:  # LF
                        idx = i
                        break
                if idx < 0:
                    break
                line_bytes = rx_buf[:idx]
                # 去掉尾部 CR
                if line_bytes and line_bytes[-1] == 13:
                    line_bytes = line_bytes[:-1]
                try:
                    line = line_bytes.decode("utf-8")
                except Exception:
                    line = ""
                if line:
                    _handle_command(line)
                rx_buf = rx_buf[idx + 1:]
            # 超长行丢弃到下一个 LF
            if len(rx_buf) > MAX_LINE_LEN:
                _enqueue_response("E FORMAT")
                # 找到下一个 LF 丢弃
                drop_to = -1
                for i, b in enumerate(rx_buf):
                    if b == 10:
                        drop_to = i
                        break
                if drop_to >= 0:
                    rx_buf = rx_buf[drop_to + 1:]
                else:
                    rx_buf = bytearray()
        except Exception as e:
            _log("hmi_rx 异常: " + str(e))
        await asyncio.sleep_ms(RX_PERIOD_MS)


async def hmi_tx():
    """发送协程（见 PC_HMI_UART_DESIGN.md 第 3 节、4.2、4.4、7.3）。"""
    global _snapshot_seq, _need_new_snapshot
    last_snapshot_ms = 0
    while True:
        now = _now_ms()

        # 每秒生成一次动态快照；G 命令置标志后也会生成
        periodic = time.ticks_diff(now, last_snapshot_ms) >= TX_PERIOD_MS
        if periodic or _need_new_snapshot:
            ln = _build_dynamic_snapshot(_snapshot_seq)
            if _enqueue_data(_dyn_buf[:ln]):
                _snapshot_seq = (_snapshot_seq + 1) & 0xFFFFFFFF
                if periodic:
                    last_snapshot_ms = now
                _need_new_snapshot = False
            else:
                _log("S 被跳过")

        # 优先发送应答槽
        resp = _dequeue_response()
        if resp:
            buf, ln = resp
            await _send_bytes(buf, ln)
        else:
            # 再发数据槽
            slot_idx = _current_data_slot()
            if slot_idx is not None:
                data = _data_slot[slot_idx]
                if isinstance(data, bytes) or isinstance(data, bytearray):
                    finished = await _send_bytes(data, _data_len[slot_idx])
                    if finished:
                        _advance_data_slot()
                elif isinstance(data, tuple) and data[0] == "ranges":
                    # 范围列表：每次发一行
                    if _data_iter[slot_idx] is None:
                        _data_iter[slot_idx] = iter(data[1])
                        _data_len[slot_idx] = len(data[1])
                    try:
                        sym, r = next(_data_iter[slot_idx])
                        line = _build_range_line(sym, r["min"], r["max"])
                        await _send_bytes(line, len(line))
                    except StopIteration:
                        # 发结束行
                        end_line = "R END {}\n".format(_data_len[slot_idx]).encode("utf-8")
                        await _send_bytes(end_line, len(end_line))
                        _advance_data_slot()

        await asyncio.sleep_ms(5)


async def _send_bytes(data, ln):
    """分块发送一行/一段数据；返回发送完成 True。"""
    pos = 0
    while pos < ln:
        end = min(pos + CHUNK_SIZE, ln)
        chunk = data[pos:end]
        try:
            HMI_UART.write(chunk)
        except Exception as e:
            _log("写 UART 失败: " + str(e))
            return False
        pos = end
        if pos < ln:
            await asyncio.sleep_ms(1)
    return True


async def hmi_watchdog():
    """看门狗协程（见 PC_HMI_UART_DESIGN.md 第 8 节）。"""
    while True:
        try:
            if state.state_data[state.ST_SYSTEM_ENABLE] == 1:
                elapsed = time.ticks_diff(_now_ms(), _last_hmi_rx_ms)
                if elapsed > WATCHDOG_TIMEOUT_MS:
                    _log("HMI 超时停机")
                    state.state_data[state.ST_SYSTEM_ENABLE] = 0
                    state.manual_params["MANUAL_MODE_ENABLE"] = 0
        except Exception as e:
            _log("watchdog 异常: " + str(e))
        await asyncio.sleep_ms(WATCHDOG_PERIOD_MS)

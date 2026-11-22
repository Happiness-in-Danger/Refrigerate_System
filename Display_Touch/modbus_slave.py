# Display_Touch/modbus_slave.py
# Modbus RTU 从机（H743, UART8, 经 UART转Modbus RTU/RS485 转接板接入总线）
# 负责：解析主站(上位机/PLC/组态软件)请求 -> 读写 modbus_regs.regs -> 按协议回复
#
# 寄存器布局（2026-09-19 重构，基准 docs/参考.csv）：
#   地址约定：报文地址 = 代码索引（0 基，无偏移）；屏上显示地址 = 索引 + 1
#   Coils     0x1-0x8    (coils[0-7]，可读写)
#   Discrete  1x1-1x16   (error_reg bit0-15 镜像，1=正常 0=故障)
#             1x100-1x105 (Driver链路/联锁/重试锁定)
#   Holding   4x1-4x223  (可读写，稀疏地址)
#   Input     3x1-3x223  (只读)
#
# 支持功能码：
#   0x01 读线圈           (coils, 只读)
#   0x02 读离散量输入     (discrete_inputs, 只读)
#   0x03 读保持寄存器     (holding_regs, 读写)
#   0x04 读输入寄存器     (input_regs, 只读)
#   0x05 写单个线圈
#   0x06 写单个保持寄存器
#   0x0F 写多个线圈
#   0x10 写多个保持寄存器

import time
import uasyncio as asyncio
from pyb import UART, Pin
from Display_Touch.modbus_regs import regs
import Board.state as state
import Board.board as board
import Board.params_store as params_store

# ─── 配置区（按现场实际情况改）───────────────────────────────
SLAVE_ADDR = 1
DE_PIN     = None   # 转接板自动流控，不需要MCU控方向

FC_READ_COILS     = 0x01
FC_READ_DISCRETE  = 0x02
FC_READ_HOLDING   = 0x03
FC_READ_INPUT     = 0x04
FC_WRITE_COIL     = 0x05
FC_WRITE_SINGLE   = 0x06
FC_WRITE_COILS    = 0x0F
FC_WRITE_MULTIPLE = 0x10

FAULT_SENTINEL = 999   # 与 Sensor/read_sensors.py, HAL/ADC_read.py 里的故障哨兵值一致

_uart = board.ModeBus_Screen

_de = Pin(DE_PIN, Pin.OUT, value=0) if DE_PIN else None


# ─── 保持寄存器范围表（2026-09-19，依据 docs/参考.csv）──────────────
# 键为报文/代码地址（= 屏上显示地址 - 1）。不在表中的地址视为保留，写入返回 0x02。
# 注：PID 增益（i16×100）范围放宽到 0-32767，避免拒绝文档默认值。
_HR_RANGE = {
    # 过热度环 4x1-4x7
    0:  (0, 32767), 1: (0, 32767), 2: (0, 32767),
    3:  (10, 100), 4: (5, 50), 5: (0, 20), 6: (1, 100),
    # 容量环 4x16-4x24
    15: (0, 32767), 16: (0, 32767), 17: (0, 32767),
    18: (500, 3000), 19: (0, 50), 20: (48, 2047), 21: (48, 2047),
    22: (10, 500), 23: (5, 60),
    # 冷凝压力环 4x25-4x30
    24: (0, 32767), 25: (0, 32767), 26: (0, 32767),
    27: (48, 2047), 28: (48, 2047), 29: (10, 500),
    # 高压 4x31-4x34
    30: (1000, 2600), 31: (1000, 2600), 32: (1000, 2600), 33: (800, 2000),
    # 保护阈值 4x50-4x62
    49: (100, 500), 50: (100, 500), 51: (100, 500), 52: (10, 100),
    53: (100, 500), 54: (100, 500), 55: (100, 500), 56: (100, 500),
    57: (8000, 15000), 58: (8000, 15000), 59: (8000, 15000),
    60: (5, 50), 61: (5, 50),
    # 延时槽 4x100-4x107
    99: (0, 3600), 100: (0, 3600), 101: (0, 3600), 102: (0, 3600),
    103: (0, 3600), 104: (0, 3600), 105: (0, 3600), 106: (0, 3600),
    # 启动参数 4x120-4x123
    119: (100, 500), 120: (48, 2047), 121: (48, 2047), 122: (48, 2047),
    # 延时槽号 4x130-4x132
    129: (0, 7), 130: (0, 7), 131: (0, 7),
    # 压缩机参数 4x150-4x157
    149: (5, 60), 150: (5, 30), 151: (10, 500),
    152: (1000, 6000), 153: (0, 32767), 154: (0, 32767),
    155: (10, 500), 156: (10, 500),
    # 风机参数 4x180-4x186
    179: (500, 3000), 180: (0, 32767), 181: (0, 32767),
    182: (10, 500), 183: (10, 500), 184: (48, 500), 185: (10, 200),
    # 时间保护 4x200-4x205
    199: (60, 600), 200: (60, 600), 201: (1, 10), 202: (1, 10),
    203: (600, 7200), 204: (10, 300),
    # 手动油门 4x220-4x223
    219: (0, 2047), 220: (0, 2047), 221: (0, 2047), 222: (0, 1000),
}

_HR_THRESHOLD_SET = {30, 31, 32, 49, 50, 51, 53, 54, 55, 56, 57, 58, 59}

# 需持久化的寄存器（配置类；排除手动指令 219-222、使能线圈）
_HR_PERSIST = (set(range(0, 7)) | set(range(15, 34)) | set(range(49, 62)) |
               set(range(99, 107)) | set(range(119, 123)) |
               set(range(129, 132)) | set(range(149, 157)) |
               set(range(179, 186)) | set(range(199, 205)))

# 需持久化的线圈（转速环/流量/晋级使能；系统使能、手动、复位脉冲不持久化）
_COIL_PERSIST = {2, 3, 4, 5, 7}


# ─── CRC16 (Modbus) ──────────────────────────────────────────
def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _send(resp: bytes):
    frame = resp + _crc16(resp).to_bytes(2, 'little')
    if _de:
        _de.value(1)
    _uart.write(frame)
    if _de:
        import time
        time.sleep_ms(max(1, int(len(frame) * 11000 / board.BAUDRATE) + 1))
        _de.value(0)


def _exception(addr, fc, code):
    _send(bytes([addr, fc | 0x80, code]))


def _to_signed16(v):
    return v - 0x10000 if v & 0x8000 else v


# ─── 功能码处理 ──────────────────────────────────────────────

def _read_bits(addr, fc, req, table):
    start = (req[2] << 8) | req[3]
    qty   = (req[4] << 8) | req[5]
    if qty < 1 or qty > 2000:
        _exception(addr, fc, 0x03); return
    bits = regs.read_block(table, start, qty)
    if bits is None:
        _exception(addr, fc, 0x02); return
    nbytes = (qty + 7) // 8
    data = bytearray(nbytes)
    for i, v in enumerate(bits):
        if v:
            data[i // 8] |= (1 << (i % 8))
    _send(bytes([addr, fc, nbytes]) + bytes(data))


def _handle_read_regs(addr, fc, req, table):
    start = (req[2] << 8) | req[3]
    qty   = (req[4] << 8) | req[5]
    if qty < 1 or qty > 125:
        _exception(addr, fc, 0x03); return
    vals = regs.read_block(table, start, qty)
    if vals is None:
        _exception(addr, fc, 0x02); return
    data = bytearray()
    for v in vals:
        v16 = v & 0xFFFF
        data += bytes([(v16 >> 8) & 0xFF, v16 & 0xFF])
    _send(bytes([addr, fc, len(data)]) + bytes(data))


def _handle_write_single_coil(addr, fc, req):
    coil = (req[2] << 8) | req[3]
    val  = (req[4] << 8) | req[5]
    if val != 0xFF00 and val != 0x0000:
        _exception(addr, fc, 0x03); return
    code = _write_coils_and_apply(coil, [1 if val == 0xFF00 else 0])
    if code:
        _exception(addr, fc, code); return
    _send(bytes(req[:6]))


def _handle_write_single(addr, fc, req):
    reg   = (req[2] << 8) | req[3]
    val16 = (req[4] << 8) | req[5]
    code = _write_and_apply(reg, [_to_signed16(val16)])
    if code:
        _exception(addr, fc, code); return
    _send(bytes(req[:6]))


def _handle_write_multiple_coils(addr, fc, req):
    start  = (req[2] << 8) | req[3]
    qty    = (req[4] << 8) | req[5]
    nbytes = req[6]
    if len(req) < 7 + nbytes + 2:
        _exception(addr, fc, 0x03); return
    if nbytes != (qty + 7) // 8 or qty < 1 or qty > 1968:
        _exception(addr, fc, 0x03); return
    values = []
    for i in range(qty):
        values.append(1 if (req[7 + i // 8] & (1 << (i % 8))) else 0)
    code = _write_coils_and_apply(start, values)
    if code:
        _exception(addr, fc, code); return
    _send(bytes([addr, fc, req[2], req[3], req[4], req[5]]))


def _handle_write_multiple(addr, fc, req):
    start  = (req[2] << 8) | req[3]
    qty    = (req[4] << 8) | req[5]
    nbytes = req[6]
    expected_len = 7 + nbytes + 2   # header+data+CRC
    if len(req) < expected_len:
        _exception(addr, fc, 0x03)
        return
    if nbytes != qty * 2 or qty < 1 or qty > 123:
        _exception(addr, fc, 0x03); return
    values = []
    for i in range(qty):
        hi = req[7 + i * 2]; lo = req[8 + i * 2]
        values.append(_to_signed16((hi << 8) | lo))
    code = _write_and_apply(start, values)
    if code:
        _exception(addr, fc, code); return
    _send(bytes([addr, fc, req[2], req[3], req[4], req[5]]))


# ─── 线圈写入：范围校验 → 写入 → 联动 state ────────────────────

def _write_coils_and_apply(start, values):
    """返回 0=成功；0x02=非法地址。失败时不改变 state / coils。"""
    n = len(values)
    if start < 0 or start + n > len(regs.coils):
        return 0x02
    for i in range(n):
        regs.coils[start + i] = 1 if values[i] else 0
    changed = list(range(start, start + n))
    _sync_coils_to_state(changed)
    if _COIL_PERSIST & set(changed):
        params_store.mark_dirty()
    return 0


def _sync_coils_to_state(changed):
    """Coil 变化后同步到 state 参数字典"""
    c = regs.coils
    for idx in changed:
        if idx == 0:      # 系统使能
            state.state_data[state.ST_SYSTEM_ENABLE] = 1 if c[0] else 0
        elif idx == 1:    # 手动模式
            state.manual_params['MANUAL_MODE_ENABLE'] = 1 if c[1] else 0
        elif idx == 2:    # 压缩机转速环使能
            state.protect_params['COMP_SPEED_LOOP_ENABLE'] = 1 if c[2] else 0
        elif idx == 3:    # 冷凝风机转速环使能
            state.protect_params['COND_FAN_SPEED_LOOP_ENABLE'] = 1 if c[3] else 0
        elif idx == 4:    # 蒸发风机转速环使能
            state.protect_params['EVAP_FAN_SPEED_LOOP_ENABLE'] = 1 if c[4] else 0
        elif idx == 5:    # 流量检查使能
            state.protect_params['FLOW_ENABLE'] = 1 if c[5] else 0
        elif idx == 6:    # 故障复位（脉冲，自动清零）
            if c[6]:
                state.fault_reset_request = True
                c[6] = 0
        elif idx == 7:    # 晋级模式切换
            state.protect_params['STARTUP_ADVANCE_MODE'] = 1 if c[7] else 0


def sync_coils_from_state():
    """把 state 当前值写回 coils（定期调用，复位脉冲恒 0）"""
    c = regs.coils
    pp = state.protect_params
    c[0] = 1 if state.state_data[state.ST_SYSTEM_ENABLE] else 0
    c[1] = 1 if state.manual_params.get('MANUAL_MODE_ENABLE', 0) else 0
    c[2] = 1 if pp.get('COMP_SPEED_LOOP_ENABLE', 1) else 0
    c[3] = 1 if pp.get('COND_FAN_SPEED_LOOP_ENABLE', 1) else 0
    c[4] = 1 if pp.get('EVAP_FAN_SPEED_LOOP_ENABLE', 1) else 0
    c[5] = 1 if pp.get('FLOW_ENABLE', 0) else 0
    c[6] = 0   # 复位脉冲只写不读
    c[7] = 1 if pp.get('STARTUP_ADVANCE_MODE', 1) else 0


# ─── 保持寄存器写入：范围校验 → 写入 → 联动 state → 一致性校验 ──────

def _prospective_consistency_ok(start, values):
    """
    在不修改 state 的前提下，用当前阈值叠加本次写入，校验分级阈值单调性。
    HP/I/TD: warn < unload < trip；LP: LP_MIN < LP_UNLOAD < LP_WARN。
    """
    pp = state.protect_params
    hp = [pp["HP_WARN"], pp["HP_UNLOAD"], pp["HP_MAX"]]
    lp = [pp["LP_MIN"], pp["LP_UNLOAD"], pp["LP_WARN"]]
    cur = [pp["I_WARN"], pp["I_LIMIT"], pp["I_UNLOAD"], pp["I_MAX"]]
    td = [pp["TD_WARN"], pp["TD_UNLOAD"], pp["TD_TRIP"]]

    for i in range(len(values)):
        a = start + i
        v = values[i]
        if a == 32: hp[0] = v / 100.0
        elif a == 31: hp[1] = v / 100.0
        elif a == 30: hp[2] = v / 100.0
        elif a == 49: lp[0] = v / 100.0
        elif a == 50: lp[1] = v / 100.0
        elif a == 51: lp[2] = v / 100.0
        elif a == 56: cur[0] = v / 10.0
        elif a == 55: cur[1] = v / 10.0
        elif a == 54: cur[2] = v / 10.0
        elif a == 53: cur[3] = v / 10.0
        elif a == 59: td[0] = v / 100.0
        elif a == 58: td[1] = v / 100.0
        elif a == 57: td[2] = v / 100.0

    return (hp[0] < hp[1] < hp[2] and lp[0] < lp[1] < lp[2] and
            cur[0] < cur[1] < cur[2] < cur[3] and td[0] < td[1] < td[2])


def _write_and_apply(start, values):
    """
    返回 0=成功；0x02=非法地址（保留）；0x03=数值越界或一致性违反。
    失败时不改变 state / holding_regs。
    """
    n = len(values)

    # 1) 范围 / 地址校验
    for i in range(n):
        a = start + i
        spec = _HR_RANGE.get(a)
        if spec is None:
            return 0x02
        if not (spec[0] <= values[i] <= spec[1]):
            return 0x03

    # 2) 一致性预校验（仅触及阈值时，先校验后写入，避免回滚）
    if (_HR_THRESHOLD_SET & set(range(start, start + n)) and
            not _prospective_consistency_ok(start, values)):
        print("[Modbus] 写入拒绝：分级阈值一致性违反")
        return 0x03

    # 3) 写入 + 应用到 state
    for i in range(n):
        regs.holding_regs[start + i] = values[i]
    _apply_holding_writes(start, n)

    # 4) 标记参数变更（掉电保持防抖；仅配置类寄存器）
    if _HR_PERSIST & set(range(start, start + n)):
        params_store.mark_dirty()
    return 0


# ─── 保持寄存器写入 → 联动 state 参数字典 ─────────────────────
# 地址布局：docs/参考.csv（2026-09-19 重构）
def _apply_holding_writes(start, qty):
    t = range(start, start + qty)
    hr = regs.holding_regs

    # 4x1-4x7 过热度环 PID
    cp = state.control_params
    if 0 in t: cp['Kp'] = hr[0] / 100.0
    if 1 in t: cp['Ki'] = hr[1] / 100.0
    if 2 in t: cp['Kd'] = hr[2] / 100.0
    if 3 in t: cp['setpoint'] = hr[3] / 10.0
    if 4 in t: cp['error_threshold'] = hr[4] / 10.0
    if 5 in t: cp['deadband'] = hr[5] / 10.0
    if 6 in t: cp['max_delta'] = hr[6]

    # 4x16-4x24 容量环
    ccp = state.compressor_control_params
    if 15 in t: ccp['Kp'] = hr[15] / 100.0
    if 16 in t: ccp['Ki'] = hr[16] / 100.0
    if 17 in t: ccp['Kd'] = hr[17] / 100.0
    if 18 in t: ccp['setpoint'] = hr[18] / 100.0
    if 19 in t: ccp['deadband'] = hr[19] / 10.0
    if 20 in t: ccp['throttle_min'] = hr[20]
    if 21 in t: ccp['throttle_max'] = hr[21]
    if 22 in t: ccp['max_delta'] = hr[22]
    if 23 in t: ccp['period_s'] = hr[23]

    # 4x25-4x30 冷凝压力环
    cond = state.cond_control_params
    if 24 in t: cond['Kp'] = hr[24] / 100.0
    if 25 in t: cond['Ki'] = hr[25] / 100.0
    if 26 in t: cond['Kd'] = hr[26] / 100.0
    if 27 in t: cond['throttle_min'] = hr[27]
    if 28 in t: cond['throttle_max'] = hr[28]
    if 29 in t: cond['max_delta'] = hr[29]

    # 4x31-4x34 高压 / 冷凝压力目标
    pp = state.protect_params
    if 30 in t: pp['HP_MAX'] = hr[30] / 100.0
    if 31 in t: pp['HP_UNLOAD'] = hr[31] / 100.0
    if 32 in t: pp['HP_WARN'] = hr[32] / 100.0
    if 33 in t:
        pp['HP_TARGET'] = hr[33] / 100.0
        cond['setpoint'] = hr[33] / 100.0

    # 4x50-4x62 保护阈值
    if 49 in t: pp['LP_MIN'] = hr[49] / 100.0
    if 50 in t: pp['LP_UNLOAD'] = hr[50] / 100.0
    if 51 in t: pp['LP_WARN'] = hr[51] / 100.0
    if 52 in t: pp['LP_HYST'] = hr[52] / 100.0
    if 53 in t: pp['I_MAX'] = hr[53] / 10.0
    if 54 in t: pp['I_UNLOAD'] = hr[54] / 10.0
    if 55 in t: pp['I_LIMIT'] = hr[55] / 10.0
    if 56 in t: pp['I_WARN'] = hr[56] / 10.0
    if 57 in t: pp['TD_TRIP'] = hr[57] / 100.0
    if 58 in t: pp['TD_UNLOAD'] = hr[58] / 100.0
    if 59 in t: pp['TD_WARN'] = hr[59] / 100.0
    if 60 in t: pp['SH_MIN'] = hr[60] / 10.0
    if 61 in t: pp['SH_CRIT'] = hr[61] / 10.0

    # 4x100-4x107 延时槽
    for i in range(8):
        if 99 + i in t:
            pp['delay_slots'][i] = hr[99 + i]

    # 4x120-4x123 启动参数
    if 119 in t: pp['EXV_START_POS'] = hr[119] / 10.0
    if 120 in t: pp['COMP_START_THROTTLE'] = hr[120]
    if 121 in t: pp['COND_FAN_START_THROTTLE'] = hr[121]
    if 122 in t: pp['EVAP_FAN_START_THROTTLE'] = hr[122]

    # 4x130-4x132 延时槽号
    if 129 in t: pp['EXV_TO_COMP_DELAY_SLOT'] = hr[129]
    if 130 in t: pp['COMP_STOP_DELAY_SLOT'] = hr[130]
    if 131 in t: pp['FAN_STOP_DELAY_SLOT'] = hr[131]

    # 4x150-4x157 压缩机参数
    if 149 in t: pp['COMP_START_DELAY_S'] = hr[149]
    if 150 in t: pp['COMP_RPM_STABILITY_WINDOW_S'] = hr[150]
    if 151 in t: pp['COMP_RPM_STABILITY_THRESHOLD'] = hr[151]
    csl = state.comp_speed_loop_params
    if 152 in t:
        csl['rpm_max'] = hr[152]
        state.state_data[state.ST_MOTOR_SPEED_MAX] = hr[152]
    if 153 in t: csl['Kp'] = hr[153] / 100.0
    if 154 in t: csl['Ki'] = hr[154] / 100.0
    if 155 in t: csl['deadband'] = hr[155]
    if 156 in t: csl['throttle_max_adjust'] = hr[156]

    # 4x180-4x186 风机参数 / 回油 / 降容
    fsl = state.fan_speed_loop_params
    if 179 in t:
        fsl['rpm_max'] = hr[179]
        state.state_data[state.ST_FAN_SPEED_MAX] = hr[179]
    if 180 in t: fsl['Kp'] = hr[180] / 100.0
    if 181 in t: fsl['Ki'] = hr[181] / 100.0
    if 182 in t: fsl['deadband'] = hr[182]
    if 183 in t: fsl['throttle_max_adjust'] = hr[183]
    if 184 in t: pp['COMP_OIL_RETURN_THROTTLE'] = hr[184]
    if 185 in t: pp['UNLOAD_RATE_THROTTLE_S'] = hr[185]

    # 4x200-4x205 防短循环 / 重试 / 通讯
    if 199 in t: pp['MIN_ON_S'] = hr[199]
    if 200 in t: pp['MIN_OFF_S'] = hr[200]
    if 201 in t: pp['MAX_STARTS_PER_HOUR'] = hr[201]
    if 202 in t: pp['RETRY_MAX'] = hr[202]
    if 203 in t: pp['RETRY_WINDOW_S'] = hr[203]
    if 204 in t: pp['MODBUS_TIMEOUT_S'] = hr[204]

    # 4x220-4x223 手动油门 / EXV
    if 219 in t: state.manual_params['MANUAL_COMP_THROTTLE'] = hr[219]
    if 220 in t: state.manual_params['MANUAL_COND_FAN_THROTTLE'] = hr[220]
    if 221 in t:
        thr = hr[221]
        state.evap_fan_user_params['EVAP_FAN_USER_THROTTLE'] = thr
        state.state_data[state.ST_EVAP_FAN_SPEED_CMD] = thr
    if 222 in t: state.manual_params['MANUAL_EXV_PCT'] = hr[222] / 10.0


# ─── 收帧（按T3.5静默切帧）───────────────────────────────────

async def _read_frame():
    buf = bytearray()
    while True:
        n = _uart.any()
        if n:
            buf += _uart.read(n)
            # 总线持续有数据（噪声）时主动让出一次，避免饿死事件循环（B2）
            await asyncio.sleep_ms(0)
        else:
            if buf:
                await asyncio.sleep_ms(board._silence_ms(board.BAUDRATE))
                if _uart.any() == 0:
                    return bytes(buf)
            else:
                await asyncio.sleep_ms(2)


def _process(req: bytes):
    if len(req) < 4:
        return
    if _crc16(req[:-2]) != (req[-2] | (req[-1] << 8)):
        print("[Modbus] CRC错误，丢弃, len=", len(req))
        return
    # Step5b: 收到合法帧更新时间戳（供 Modbus 通讯断检测）
    state.last_modbus_frame_ms = time.ticks_ms()
    addr = req[0]
    is_broadcast = (addr == 0)
    if addr != SLAVE_ADDR and not is_broadcast:
        return
    fc = req[1]

    if is_broadcast:
        # 广播只允许写操作，且不回应答
        if fc == FC_WRITE_SINGLE:
            reg = (req[2] << 8) | req[3]
            val16 = (req[4] << 8) | req[5]
            _write_and_apply(reg, [_to_signed16(val16)])
        elif fc == FC_WRITE_COIL:
            coil = (req[2] << 8) | req[3]
            val = (req[4] << 8) | req[5]
            if val in (0xFF00, 0x0000):
                _write_coils_and_apply(coil, [1 if val == 0xFF00 else 0])
        elif fc == FC_WRITE_MULTIPLE:
            start = (req[2] << 8) | req[3]
            qty = (req[4] << 8) | req[5]
            nbytes = req[6]
            if (len(req) >= 7 + nbytes + 2 and nbytes == qty * 2 and
                    1 <= qty <= 123):
                values = []
                for i in range(qty):
                    hi = req[7 + i*2]; lo = req[8 + i*2]
                    values.append(_to_signed16((hi << 8) | lo))
                _write_and_apply(start, values)
        elif fc == FC_WRITE_COILS:
            start = (req[2] << 8) | req[3]
            qty = (req[4] << 8) | req[5]
            nbytes = req[6]
            if (len(req) >= 7 + nbytes + 2 and nbytes == (qty + 7) // 8 and
                    1 <= qty <= 1968):
                values = [1 if (req[7 + i // 8] & (1 << (i % 8))) else 0
                          for i in range(qty)]
                _write_coils_and_apply(start, values)
        return  # 广播不回复，无论什么功能码

    if fc == FC_READ_COILS:
        _read_bits(addr, fc, req, regs.coils)
    elif fc == FC_READ_DISCRETE:
        _read_bits(addr, fc, req, regs.discrete_inputs)
    elif fc == FC_READ_HOLDING:
        _handle_read_regs(addr, fc, req, regs.holding_regs)
    elif fc == FC_READ_INPUT:
        _handle_read_regs(addr, fc, req, regs.input_regs)
    elif fc == FC_WRITE_COIL:
        _handle_write_single_coil(addr, fc, req)
    elif fc == FC_WRITE_SINGLE:
        _handle_write_single(addr, fc, req)
    elif fc == FC_WRITE_COILS:
        _handle_write_multiple_coils(addr, fc, req)
    elif fc == FC_WRITE_MULTIPLE:
        _handle_write_multiple(addr, fc, req)
    else:
        _exception(addr, fc, 0x01)


async def slave_loop():
    print("[Modbus] RTU从机启动 addr=%d baud=%d UART8" % (SLAVE_ADDR, board.BAUDRATE))
    while True:
        req = await _read_frame()
        if req:
            try:
                _process(req)
            except Exception as e:
                print("[Modbus] 处理异常:", e)


# ─── state.py -> regs 同步 ────────────────────────────────────
def _fixed100(v):
    """浮点值*100定点化，故障哨兵值999原样透传，其余限幅到int16范围"""
    if v == FAULT_SENTINEL:
        return FAULT_SENTINEL
    v16 = int(round(v * 100))
    return max(-32768, min(32767, v16))


def _fixed10(v):
    """浮点值*10定点化，故障哨兵值999原样透传，其余限幅到int16范围"""
    if v == FAULT_SENTINEL:
        return FAULT_SENTINEL
    v16 = int(round(v * 10))
    return max(-32768, min(32767, v16))


def _fixed100_u(v):
    """非负值*100定点化，哨兵999原样透传，限幅到 u16 (0-65535)。
    用于焓值等正值通道：R134a 焓值 ×100 可达 ~48000，超出 int16。"""
    if v == FAULT_SENTINEL:
        return FAULT_SENTINEL
    v16 = int(round(v * 100))
    return max(0, min(65535, v16))


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

    # 3x10 进口焓（吸气焓）/ 3x11 出口焓（液管焓）*100（u16，焓值可超 int16）
    ir[9] = _fixed100_u(sd[state.IDX_E_VALUE])
    ir[10] = _fixed100_u(state.liquid_enthalpy)

    # 3x12 EXV 当前开度 / 3x13 EXV 目标开度 *10
    ir[11] = _fixed10(st[state.ST_VALVE_POS])
    ir[12] = _fixed10(state.exv_target_pct)

    # 3x14 整机电流 *10
    ir[13] = _fixed10(sd[state.IDX_CURRENT])

    # 3x15-3x16 质量码（每通道 2 bit；0=OK 1=SUSPECT 2=BAD）
    q_low = 0
    for i in range(8):
        q_low |= (state.sensor_quality[i] & 0x03) << (2 * i)
    ir[14] = q_low
    q_high = 0
    for i in range(8, 10):
        q_high |= (state.sensor_quality[i] & 0x03) << (2 * (i - 8))
    ir[15] = q_high

    # 3x17 传感器分通道故障位图（完整 16-bit）
    ir[16] = state.sensor_fault_reg

    # 3x100-3x102 系统模式 / 启动阶段 / RUNNING 子状态
    ir[99] = state.sys_mode
    ir[100] = state.startup_stage
    ir[101] = state.running_substate

    # 3x110-3x111 32-bit error_reg 拆成低/高 16 位
    ir[109] = state.error_reg & 0xFFFF
    ir[110] = (state.error_reg >> 16) & 0xFFFF

    # 3x200-3x205 执行机构状态（压缩机/风机 油门/转速）
    ir[199] = state.comp_throttle_out
    ir[200] = st[state.ST_MOTOR_SPEED]
    ir[201] = state.evap_fan_throttle_out
    ir[202] = st[state.ST_EVAP_FAN_RPM]
    ir[203] = state.cond_fan_throttle_out
    ir[204] = st[state.ST_COND_FAN_RPM]
    ir[205] = 0

    # 3x207-3x222 ESC 遥测（state_data 16-31）
    for i in range(16):
        ir[206 + i] = st[16 + i]

    # 3x223 L1 限载动作码位图
    ir[222] = state.limit_actions

    # di0-di15: error_reg 位镜像（1=正常，0=故障）
    er = state.error_reg
    di = regs.discrete_inputs
    for bit in range(16):
        di[bit] = 1 if (er & (1 << bit)) else 0

    # di99-di104: 1x100-1x105 链路/联锁/重试状态
    il = state.supervisor_interlocks
    di[99] = 1 if state.driver_link_ok else 0
    di[100] = 1 if il["comp_enable"] else 0
    di[101] = 1 if il["evap_fan_enable"] else 0
    di[102] = 1 if il["cond_fan_enable"] else 0
    di[103] = 1 if il["exv_enable"] else 0
    di[104] = 1 if state.retry_lockout else 0


def sync_holding_readback():
    """把当前生效参数写回 holding_regs，供上位机只读展示/核对"""
    hr = regs.holding_regs
    pp = state.protect_params
    cp = state.control_params
    ccp = state.compressor_control_params
    cond = state.cond_control_params
    csl = state.comp_speed_loop_params
    fsl = state.fan_speed_loop_params

    # 4x1-4x7 过热度环
    hr[0] = int(round(cp['Kp'] * 100))
    hr[1] = int(round(cp['Ki'] * 100))
    hr[2] = int(round(cp['Kd'] * 100))
    hr[3] = int(round(cp['setpoint'] * 10))
    hr[4] = int(round(cp['error_threshold'] * 10))
    hr[5] = int(round(cp['deadband'] * 10))
    hr[6] = int(round(cp['max_delta']))

    # 4x16-4x24 容量环
    hr[15] = int(round(ccp['Kp'] * 100))
    hr[16] = int(round(ccp['Ki'] * 100))
    hr[17] = int(round(ccp['Kd'] * 100))
    hr[18] = int(round(ccp['setpoint'] * 100))
    hr[19] = int(round(ccp['deadband'] * 10))
    hr[20] = ccp['throttle_min']
    hr[21] = ccp['throttle_max']
    hr[22] = int(round(ccp['max_delta']))
    hr[23] = int(round(ccp.get('period_s', 10.0)))

    # 4x25-4x30 冷凝压力环
    hr[24] = int(round(cond['Kp'] * 100))
    hr[25] = int(round(cond['Ki'] * 100))
    hr[26] = int(round(cond['Kd'] * 100))
    hr[27] = cond['throttle_min']
    hr[28] = cond['throttle_max']
    hr[29] = int(round(cond['max_delta']))

    # 4x31-4x34 高压 / 冷凝压力目标
    hr[30] = int(round(pp['HP_MAX'] * 100))
    hr[31] = int(round(pp['HP_UNLOAD'] * 100))
    hr[32] = int(round(pp['HP_WARN'] * 100))
    hr[33] = int(round(pp['HP_TARGET'] * 100))

    # 4x50-4x62 保护阈值
    hr[49] = int(round(pp['LP_MIN'] * 100))
    hr[50] = int(round(pp['LP_UNLOAD'] * 100))
    hr[51] = int(round(pp['LP_WARN'] * 100))
    hr[52] = int(round(pp['LP_HYST'] * 100))
    hr[53] = int(round(pp['I_MAX'] * 10))
    hr[54] = int(round(pp['I_UNLOAD'] * 10))
    hr[55] = int(round(pp['I_LIMIT'] * 10))
    hr[56] = int(round(pp['I_WARN'] * 10))
    hr[57] = int(round(pp['TD_TRIP'] * 100))
    hr[58] = int(round(pp['TD_UNLOAD'] * 100))
    hr[59] = int(round(pp['TD_WARN'] * 100))
    hr[60] = int(round(pp['SH_MIN'] * 10))
    hr[61] = int(round(pp['SH_CRIT'] * 10))

    # 4x100-4x107 延时槽
    slots = pp['delay_slots']
    for i in range(8):
        hr[99 + i] = slots[i]

    # 4x120-4x123 启动参数
    hr[119] = int(round(pp['EXV_START_POS'] * 10))
    hr[120] = pp['COMP_START_THROTTLE']
    hr[121] = pp['COND_FAN_START_THROTTLE']
    hr[122] = pp['EVAP_FAN_START_THROTTLE']

    # 4x130-4x132 延时槽号
    hr[129] = pp['EXV_TO_COMP_DELAY_SLOT']
    hr[130] = pp['COMP_STOP_DELAY_SLOT']
    hr[131] = pp['FAN_STOP_DELAY_SLOT']

    # 4x150-4x157 压缩机参数
    hr[149] = int(round(pp['COMP_START_DELAY_S']))
    hr[150] = int(round(pp['COMP_RPM_STABILITY_WINDOW_S']))
    hr[151] = pp['COMP_RPM_STABILITY_THRESHOLD']
    hr[152] = csl['rpm_max']
    hr[153] = int(round(csl['Kp'] * 100))
    hr[154] = int(round(csl['Ki'] * 100))
    hr[155] = csl['deadband']
    hr[156] = csl['throttle_max_adjust']

    # 4x180-4x186 风机参数 / 回油 / 降容
    hr[179] = fsl['rpm_max']
    hr[180] = int(round(fsl['Kp'] * 100))
    hr[181] = int(round(fsl['Ki'] * 100))
    hr[182] = fsl['deadband']
    hr[183] = fsl['throttle_max_adjust']
    hr[184] = pp['COMP_OIL_RETURN_THROTTLE']
    hr[185] = pp['UNLOAD_RATE_THROTTLE_S']

    # 4x200-4x205 防短循环 / 重试 / 通讯
    hr[199] = int(round(pp['MIN_ON_S']))
    hr[200] = int(round(pp['MIN_OFF_S']))
    hr[201] = pp['MAX_STARTS_PER_HOUR']
    hr[202] = pp['RETRY_MAX']
    hr[203] = pp['RETRY_WINDOW_S']
    hr[204] = int(round(pp['MODBUS_TIMEOUT_S']))

    # 4x220-4x223 手动油门 / EXV
    hr[219] = state.manual_params['MANUAL_COMP_THROTTLE']
    hr[220] = state.manual_params['MANUAL_COND_FAN_THROTTLE']
    hr[221] = state.evap_fan_user_params['EVAP_FAN_USER_THROTTLE']
    hr[222] = int(round(state.manual_params['MANUAL_EXV_PCT'] * 10))


async def sync_loop(period_ms=200):
    while True:
        sync_coils_from_state()
        sync_input_regs()
        sync_holding_readback()
        await asyncio.sleep_ms(period_ms)

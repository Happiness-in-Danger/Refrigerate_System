# Display_Touch/modbus_slave.py
# Modbus RTU 从机（H743, UART8, 经 UART转Modbus RTU/RS485 转接板接入总线）
# 负责：解析主站(上位机/PLC/组态软件)请求 -> 读写 modbus_regs.regs -> 按协议回复
#
# 寄存器布局定义见 modbus_regs.py（以 Board/state.py 为准，两边保持严格对齐）
#
# 支持功能码：
#   0x02 读离散量输入   (discrete_inputs, 只读) —— 暂未使用，保留
#   0x03 读保持寄存器   (holding_regs,   读写) —— control_params
#   0x04 读输入寄存器   (input_regs,     只读) —— sensor_data / state_data /
#                                                  error_reg / sensor_fault_reg
#   0x06 写单个保持寄存器
#   0x10 写多个保持寄存器
#
# main.py 用法示例：
#   import Display_Touch.modbus_slave as modbus_slave
#   ...
#   await asyncio.gather(
#       Compressor(), Exv(), Display(), Sensor(), Log(),
#       modbus_slave.slave_loop(),
#       modbus_slave.sync_loop(),
#   )

import uasyncio as asyncio
from pyb import UART, Pin
from Display_Touch.modbus_regs import regs
import Board.state as state

# ─── 配置区（按现场实际情况改）───────────────────────────────
SLAVE_ADDR = 1
BAUDRATE   = 9600
DE_PIN     = None   # 转接板自动流控，不需要MCU控方向

FC_READ_DISCRETE  = 0x02
FC_READ_HOLDING   = 0x03
FC_READ_INPUT     = 0x04
FC_WRITE_SINGLE   = 0x06
FC_WRITE_MULTIPLE = 0x10

# aggr_mode 字符串 <-> holding_regs[7] 整数枚举 的映射
_AGGR_MODE_TO_INT = {"off": 0, "proportional": 1, "togoal": 2}
_AGGR_MODE_FROM_INT = {v: k for k, v in _AGGR_MODE_TO_INT.items()}

FAULT_SENTINEL = 999   # 与 Sensor/read_sensors.py, HAL/ADC_read.py 里的故障哨兵值一致


def _silence_ms(baud):
    """T3.5 帧间静默时间（ms），用来判断一帧是否收完"""
    if baud > 19200:
        return 2
    t_char_ms = 11000.0 / baud
    return max(2, int(t_char_ms * 3.5) + 1)


_uart = UART(8, BAUDRATE, bits=8, parity=None, stop=1,
             timeout=2, timeout_char=_silence_ms(BAUDRATE), read_buf_len=256)

_de = Pin(DE_PIN, Pin.OUT, value=0) if DE_PIN else None


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
        time.sleep_ms(max(1, int(len(frame) * 11000 / BAUDRATE) + 1))
        _de.value(0)


def _exception(addr, fc, code):
    _send(bytes([addr, fc | 0x80, code]))


def _to_signed16(v):
    return v - 0x10000 if v & 0x8000 else v


# ─── 功能码处理 ──────────────────────────────────────────────

def _handle_read_bits(addr, fc, req):
    start = (req[2] << 8) | req[3]
    qty   = (req[4] << 8) | req[5]
    if qty < 1 or qty > 2000:
        _exception(addr, fc, 0x03); return
    bits = regs.read_block(regs.discrete_inputs, start, qty)
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


def _handle_write_single(addr, fc, req):
    reg   = (req[2] << 8) | req[3]
    val16 = (req[4] << 8) | req[5]
    ok = regs.write_single(regs.holding_regs, reg, _to_signed16(val16))
    if not ok:
        _exception(addr, fc, 0x02); return
    _apply_holding_writes(reg, 1)
    _send(bytes(req[:6]))


def _handle_write_multiple(addr, fc, req):
    start  = (req[2] << 8) | req[3]
    qty    = (req[4] << 8) | req[5]
    nbytes = req[6]
    if nbytes != qty * 2 or qty < 1 or qty > 123:
        _exception(addr, fc, 0x03); return
    values = []
    for i in range(qty):
        hi = req[7 + i * 2]; lo = req[8 + i * 2]
        values.append(_to_signed16((hi << 8) | lo))
    ok = regs.write_block(regs.holding_regs, start, values)
    if not ok:
        _exception(addr, fc, 0x02); return
    _apply_holding_writes(start, qty)
    _send(bytes([addr, fc, req[2], req[3], req[4], req[5]]))


# ─── 保持寄存器写入 → 联动 state.control_params ──────────────
# 地址布局见 modbus_regs.py 顶部注释，严格对齐 state.control_params 字典的字段
def _apply_holding_writes(start, qty):
    touched = range(start, start + qty)
    cp = state.control_params
    if 0 in touched: cp['Kp']              = regs.holding_regs[0] / 100
    if 1 in touched: cp['Ki']              = regs.holding_regs[1] / 100
    if 2 in touched: cp['Kd']              = regs.holding_regs[2] / 100
    if 3 in touched: cp['setpoint']        = regs.holding_regs[3] / 100
    if 4 in touched: cp['deadband']        = regs.holding_regs[4] / 100
    if 5 in touched: cp['max_delta']       = regs.holding_regs[5] / 100
    if 6 in touched: cp['error_threshold'] = regs.holding_regs[6] / 100
    if 7 in touched:
        mode = _AGGR_MODE_FROM_INT.get(regs.holding_regs[7])
        if mode is not None:
            cp['aggr_mode'] = mode
    # 注：目前没有任何协程把 control_params 的新值应用回运行中的 PID 控制器实例，
    # 这里只是把写入值同步进 state.control_params，实际生效还需要你在 main.py
    # 里加一个周期性协程，把 control_params 写回 ctrl_exv.Kp/Ki/Kd/...


# ─── 收帧（按T3.5静默切帧）───────────────────────────────────

async def _read_frame():
    buf = bytearray()
    while True:
        n = _uart.any()
        if n:
            buf += _uart.read(n)
        else:
            if buf:
                await asyncio.sleep_ms(_silence_ms(BAUDRATE))
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
    addr = req[0]
    if addr != SLAVE_ADDR and addr != 0:
        return
    fc = req[1]

    if fc == FC_READ_DISCRETE:
        _handle_read_bits(addr, fc, req)
    elif fc == FC_READ_HOLDING:
        _handle_read_regs(addr, fc, req, regs.holding_regs)
    elif fc == FC_READ_INPUT:
        _handle_read_regs(addr, fc, req, regs.input_regs)
    elif fc == FC_WRITE_SINGLE:
        _handle_write_single(addr, fc, req)
    elif fc == FC_WRITE_MULTIPLE:
        _handle_write_multiple(addr, fc, req)
    else:
        _exception(addr, fc, 0x01)


async def slave_loop():
    print("[Modbus] RTU从机启动 addr=%d baud=%d UART8" % (SLAVE_ADDR, BAUDRATE))
    while True:
        req = await _read_frame()
        if req:
            try:
                _process(req)
            except Exception as e:
                print("[Modbus] 处理异常:", e)


# ─── state.py -> regs.input_regs 同步 ────────────────────────
def _fixed100(v):
    """浮点值*100定点化，故障哨兵值999原样透传，其余限幅到int16范围"""
    if v == FAULT_SENTINEL:
        return FAULT_SENTINEL
    v16 = int(round(v * 100))
    return max(-32768, min(32767, v16))


def sync_input_regs():
    sd = state.sensor_data
    st = state.state_data
    ir = regs.input_regs

    # 0-9: sensor_data[0..9] *100（含电流），故障哨兵999原样透传
    for i in range(10):
        ir[i] = _fixed100(sd[i])

    # 10-15 预留，不动

    # 16-17: 两个故障寄存器整字直接透传，上位机自己按位与取出各报警位
    ir[16] = state.error_reg
    ir[17] = state.sensor_fault_reg

    # 18-22: 阀位/风机/压缩机状态
    ir[18] = _fixed100(st[state.ST_VALVE_POS])       # 开度%，*100
    ir[19] = st[state.ST_VALVE_POS_STEPS]             # 步数，原值
    ir[20] = st[state.ST_EVAP_FAN_RPM]                 # rpm，原值
    ir[21] = st[state.ST_COND_FAN_RPM]                 # rpm，原值
    ir[22] = st[state.ST_MOTOR_SPEED]                  # rpm，原值

    # 23-31 预留，不动


def sync_holding_readback():
    """把当前生效的 control_params 写回 holding_regs，供上位机只读展示/核对"""
    cp = state.control_params
    hr = regs.holding_regs
    hr[0] = int(round(cp['Kp'] * 100))
    hr[1] = int(round(cp['Ki'] * 100))
    hr[2] = int(round(cp['Kd'] * 100))
    hr[3] = int(round(cp['setpoint'] * 100))
    hr[4] = int(round(cp['deadband'] * 100))
    hr[5] = int(round(cp['max_delta'] * 100))
    hr[6] = int(round(cp['error_threshold'] * 100))
    hr[7] = _AGGR_MODE_TO_INT.get(cp['aggr_mode'], 0)


async def sync_loop(period_ms=200):
    while True:
        sync_input_regs()
        sync_holding_readback()
        await asyncio.sleep_ms(period_ms)

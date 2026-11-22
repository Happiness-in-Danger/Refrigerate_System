#!/usr/bin/env python3
"""
Step7 Verification Checklist (offline / PC)

Step7 DoD（依据 PLAN.md §2 / STEP6_SEAL.md §6）：
1. 收尾：Desplay.py → Display.py 更名（D3），函数 desplay → display
2. A8：故障哨兵 999 原样透传，不再 ×100 溢出 u16
3. Modbus 从机字节级协议：FC01/02/03/04/05/06/0F/10 + 广播 + 坏 CRC + 异常码
4. 4x223 手动 EXV 写入通道（阀动测试的软件侧；实际阀动需上板脱开阀体）
5. 死代码仅标记不删除（HAL/PWM.py、Sensor/tem_lastV02_STM32.py 仍存在）
6. pymodbus 上板主机脚本存在

2026-09-19 Modbus 重构：地址基准改为 docs/参考.csv（报文地址 = 代码索引，0 基）。

本脚本只做静态检查与 mock UART 的字节级测试，不连接硬件。
pymodbus 全 FC 总线实测见 test/test_step7_pymodbus_host.py（需 H743 + RS485）。
"""

import sys
import os
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ═══════════════════════════════════════════════════════════
# Mock MicroPython modules
# ═══════════════════════════════════════════════════════════
_start_ticks = int(_time.time() * 1000)
_mock_now = [_start_ticks]


def ticks_ms():
    return _mock_now[0]


def ticks_diff(a, b):
    return a - b


def sleep_ms(ms):
    pass


class MockTime:
    ticks_ms = staticmethod(ticks_ms)
    ticks_diff = staticmethod(ticks_diff)
    sleep_ms = staticmethod(sleep_ms)
    sleep = staticmethod(_time.sleep)


sys.modules['time'] = MockTime()


class MockPin:
    OUT = 1
    IN = 0

    def __init__(self, name, mode=None, value=None):
        self.name = name
        self._value = value or 0

    def value(self, v=None):
        if v is not None:
            self._value = v
        return self._value


class MockADC:
    def __init__(self, pin):
        self._value = 32768

    def read(self):
        return self._value


class MockUART:
    def __init__(self, *args, **kwargs):
        self._buf = b''

    def any(self):
        return len(self._buf)

    def read(self, n=None):
        if n is None:
            n = len(self._buf)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def write(self, data):
        pass


class MockSPI:
    MASTER = 0

    def __init__(self, *args, **kwargs):
        pass

    def write(self, data):
        pass

    def read(self, n):
        return b'\x00' * n


class MockSoftSPI(MockSPI):
    pass


class MockMachine:
    SoftSPI = MockSoftSPI


class MockADCDirect:
    @staticmethod
    def read_pc2_c():
        return 32768

    @staticmethod
    def read_pc3_c():
        return 32768


sys.modules['pyb'] = type(sys)('pyb')
sys.modules['pyb'].Pin = MockPin
sys.modules['pyb'].ADC = MockADC
sys.modules['pyb'].UART = MockUART
sys.modules['pyb'].SPI = MockSPI
sys.modules['machine'] = MockMachine()
sys.modules['adc_direct'] = MockADCDirect()
sys.modules['uasyncio'] = type(sys)('uasyncio')

import Board.state as state
from Display_Touch import modbus_slave as ms
from Display_Touch.modbus_regs import regs
from Display_Touch import Display as disp

# ═══════════════════════════════════════════════════════════
# Test utilities
# ═══════════════════════════════════════════════════════════
_test_count = 0
_pass_count = 0
_fail_count = 0


def check(name, cond, detail=""):
    global _test_count, _pass_count, _fail_count
    _test_count += 1
    prefix = "[%02d]" % _test_count
    if cond:
        print("%s PASS  %s" % (prefix, name))
        _pass_count += 1
    else:
        print("%s FAIL  %s%s" % (prefix, name, (" — " + detail) if detail else ""))
        _fail_count += 1


def read_file(rel_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, rel_path), "r") as f:
        return f.read()


def file_exists(rel_path):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.exists(os.path.join(root, rel_path))


def reset_state():
    state.sys_mode = state.MODE_SAFE_STOP
    state.error_reg = 0xFFFFFFFF
    state.sensor_fault_reg = 0xFFFF
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    state.fault_reset_request = False
    state.exv_target_pct = 0.0
    state.subcooling = 999
    state.liquid_enthalpy = 999
    state.manual_params['MANUAL_MODE_ENABLE'] = 0
    state.manual_params['MANUAL_COMP_THROTTLE'] = 0
    state.manual_params['MANUAL_COND_FAN_THROTTLE'] = 0
    state.manual_params['MANUAL_EXV_PCT'] = 300
    state.protect_params['COMP_SPEED_LOOP_ENABLE'] = 1
    state.protect_params['COND_FAN_SPEED_LOOP_ENABLE'] = 1
    state.protect_params['EVAP_FAN_SPEED_LOOP_ENABLE'] = 1
    state.protect_params['FLOW_ENABLE'] = 0
    state.protect_params['STARTUP_ADVANCE_MODE'] = 1
    for i in range(len(regs.holding_regs)):
        regs.holding_regs[i] = 0
    for i in range(len(regs.input_regs)):
        regs.input_regs[i] = 0
    for i in range(len(regs.discrete_inputs)):
        regs.discrete_inputs[i] = 0
    for i in range(len(regs.coils)):
        regs.coils[i] = 0


class CaptureUART:
    def __init__(self):
        self.frames = []

    def any(self):
        return 0

    def read(self, n=None):
        return b''

    def write(self, data):
        self.frames.append(bytes(data))

    def last(self):
        return self.frames[-1] if self.frames else None


def build_frame(addr, fc, payload):
    body = bytes([addr, fc]) + bytes(payload)
    crc = ms._crc16(body)
    return body + crc.to_bytes(2, 'little')


def crc_ok(frame):
    return ms._crc16(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


# ═══════════════════════════════════════════════════════════
# 1. Rename (D3)
# ═══════════════════════════════════════════════════════════

def test_rename():
    check("Display_Touch/Display.py 存在", file_exists("Display_Touch/Display.py"))
    check("Display_Touch/Desplay.py 已删除", not file_exists("Display_Touch/Desplay.py"))
    main_src = read_file("main.py")
    check("main.py 导入 Display.display",
          "from Display_Touch.Display import display" in main_src)
    check("main.py 调用 display()", "display()" in main_src)
    check("main.py 不再引用 desplay", "desplay" not in main_src.lower())
    check("Display.py 定义 display()", callable(disp.display))
    check("Display.py 不再定义 desplay", not hasattr(disp, "desplay"))


# ═══════════════════════════════════════════════════════════
# 2. A8 sentinel passthrough
# ═══════════════════════════════════════════════════════════

def test_a8_sentinel():
    check("_scale(999) 原样透传", disp._scale(999) == 999)
    check("_scale(12.34) ×100", disp._scale(12.34) == 1234)

    cap = CaptureUART()
    old = disp.uart
    disp.uart = cap
    try:
        # u16 通道：哨兵 999 不溢出
        disp.uart_write([999], [(0x0001, 'u16')])
        frame = cap.last()
        value = (frame[6] << 8) | frame[7]
        check("A8 u16 哨兵 999 → 帧值 999（非 99900）", value == 999,
              "value=%d" % value)
        check("A8 帧头 A5 5A 05 82",
              frame[:4] == bytes([0xA5, 0x5A, 0x05, 0x82]))

        # 正常值 ×100
        disp.uart_write([12.34], [(0x0002, 'u16')])
        frame = cap.last()
        value = (frame[6] << 8) | frame[7]
        check("A8 正常值 12.34 → 1234", value == 1234, "value=%d" % value)

        # u32 通道哨兵
        disp.uart_write([999], [(0x0010, 'u32')])
        frame = cap.last()
        value = ((frame[6] << 24) | (frame[7] << 16) |
                 (frame[8] << 8) | frame[9])
        check("A8 u32 哨兵 999 → 帧值 999", value == 999, "value=%d" % value)
    finally:
        disp.uart = old


# ═══════════════════════════════════════════════════════════
# 3. Modbus byte-level protocol (mock UART)
# ═══════════════════════════════════════════════════════════

def _with_capture(fn):
    cap = CaptureUART()
    old = ms._uart
    ms._uart = cap
    try:
        return fn(cap)
    finally:
        ms._uart = old


def test_fc01_read_coils():
    def body(cap):
        reset_state()
        regs.coils[0] = 1
        regs.coils[3] = 1
        frame = build_frame(1, 0x01, [0x00, 0, 0x00, 8])
        ms._process(frame)
        resp = cap.last()
        check("FC01 有应答", resp is not None)
        check("FC01 功能码=0x01", resp[1] == 0x01)
        check("FC01 字节数=1", resp[2] == 1)
        bits = resp[3]
        check("FC01 coil0=1", (bits & 0x01) == 1)
        check("FC01 coil1=0", (bits & 0x02) == 0)
        check("FC01 coil3=1", (bits & 0x08) == 0x08)
    _with_capture(body)


def test_fc03_read_holding():
    def body(cap):
        reset_state()
        regs.holding_regs[30] = 2150
        regs.holding_regs[31] = 2050
        frame = build_frame(1, 0x03, [0x00, 30, 0x00, 2])
        ms._process(frame)
        resp = cap.last()
        check("FC03 有应答", resp is not None)
        check("FC03 地址/功能码回显", resp[:2] == bytes([1, 0x03]))
        check("FC03 字节数=4", resp[2] == 4)
        v0 = (resp[3] << 8) | resp[4]
        v1 = (resp[5] << 8) | resp[6]
        check("FC03 hr30=2150", v0 == 2150, "v0=%d" % v0)
        check("FC03 hr31=2050", v1 == 2050, "v1=%d" % v1)
        check("FC03 CRC 正确", crc_ok(resp))
    _with_capture(body)


def test_fc04_read_input():
    def body(cap):
        reset_state()
        state.sensor_data[0] = 12.34
        state.sensor_data[state.IDX_E_VALUE] = 420.5
        state.sensor_data[state.IDX_CURRENT] = 999
        state.liquid_enthalpy = 258.3
        state.sensor_quality[0] = state.Q_OK
        ms.sync_input_regs()
        frame = build_frame(1, 0x04, [0x00, 0, 0x00, 17])
        ms._process(frame)
        resp = cap.last()
        check("FC04 有应答", resp is not None)
        check("FC04 功能码=0x04", resp[1] == 0x04)
        v0 = (resp[3] << 8) | resp[4]
        v9 = (resp[3 + 9 * 2] << 8) | resp[4 + 9 * 2]
        v10 = (resp[3 + 10 * 2] << 8) | resp[4 + 10 * 2]
        v13 = (resp[3 + 13 * 2] << 8) | resp[4 + 13 * 2]
        check("FC04 ir0=1234", v0 == 1234, "v0=%d" % v0)
        check("FC04 ir9 进口焓 ×100=42050", v9 == 42050, "v9=%d" % v9)
        check("FC04 ir10 出口焓 ×100=25830", v10 == 25830, "v10=%d" % v10)
        check("FC04 ir13 电流哨兵 999 透传", v13 == 999, "v13=%d" % v13)
    _with_capture(body)


def test_fc02_read_discrete():
    def body(cap):
        reset_state()
        state.error_reg = 0xFFFFFFFF & ~(1 << 1)   # bit1 故障
        ms.sync_input_regs()
        frame = build_frame(1, 0x02, [0x00, 0, 0x00, 16])
        ms._process(frame)
        resp = cap.last()
        check("FC02 有应答", resp is not None)
        check("FC02 功能码=0x02", resp[1] == 0x02)
        check("FC02 字节数=2", resp[2] == 2)
        bits = resp[3] | (resp[4] << 8)
        check("FC02 di0=1（bit0 正常）", (bits & 0x01) == 1)
        check("FC02 di1=0（bit1 故障）", (bits & 0x02) == 0)
        check("FC02 di2=1", (bits & 0x04) == 0x04)
    _with_capture(body)


def test_fc05_write_coil():
    def body(cap):
        reset_state()
        frame = build_frame(1, 0x05, [0x00, 0, 0xFF, 0x00])   # coil0 = ON
        ms._process(frame)
        resp = cap.last()
        check("FC05 有应答", resp is not None)
        check("FC05 回显请求", resp[:6] == frame[:6])
        check("FC05 coil0=ON → ST_SYSTEM_ENABLE=1",
              state.state_data[state.ST_SYSTEM_ENABLE] == 1)
        # 非法值 → 0x03
        ms._process(build_frame(1, 0x05, [0x00, 0, 0x12, 0x34]))
        resp = cap.last()
        check("FC05 非法值返回异常 0x03",
              resp is not None and resp[1] == (0x05 | 0x80) and resp[2] == 0x03,
              "resp=%r" % (resp,))
    _with_capture(body)


def test_fc06_write_single():
    def body(cap):
        reset_state()
        frame = build_frame(1, 0x06, [0x00, 0, 0x00, 0x3C])   # hr0 = 60
        ms._process(frame)
        resp = cap.last()
        check("FC06 有应答", resp is not None)
        check("FC06 回显请求", resp[:6] == frame[:6])
        check("FC06 CRC 正确", crc_ok(resp))
        check("FC06 hr0 → SH Kp=0.6",
              abs(state.control_params['Kp'] - 0.6) < 1e-6)
    _with_capture(body)


def test_fc06_range_reject():
    def body(cap):
        reset_state()
        # hr31 HP_UNLOAD 上限 2600，写 9999 越界
        frame = build_frame(1, 0x06, [0x00, 31, 0x27, 0x0F])
        ms._process(frame)
        resp = cap.last()
        check("FC06 越界返回异常码 0x03",
              resp is not None and resp[1] == (0x06 | 0x80) and resp[2] == 0x03,
              "resp=%r" % (resp,))
        check("FC06 越界不改变 HP_UNLOAD",
              abs(state.protect_params['HP_UNLOAD'] - 20.5) < 1e-6)
    _with_capture(body)


def test_fc0f_write_coils():
    def body(cap):
        reset_state()
        # 写 coil2..coil5 = ON,ON,ON,ON  (bit0-3 -> 0x0F)
        frame = build_frame(1, 0x0F, [0x00, 2, 0x00, 4, 0x01, 0x0F])
        ms._process(frame)
        resp = cap.last()
        check("FC0F 有应答", resp is not None)
        check("FC0F 回显起始+数量",
              resp[2:6] == bytes([0x00, 2, 0x00, 4]), "resp=%r" % (resp,))
        check("FC0F 转速环/流量使能已写",
              state.protect_params['COMP_SPEED_LOOP_ENABLE'] == 1 and
              state.protect_params['COND_FAN_SPEED_LOOP_ENABLE'] == 1 and
              state.protect_params['EVAP_FAN_SPEED_LOOP_ENABLE'] == 1 and
              state.protect_params['FLOW_ENABLE'] == 1)
    _with_capture(body)


def test_fc10_write_multiple():
    def body(cap):
        reset_state()
        # hr99-hr101 = 1,2,3 (4x100-4x102 延时槽)
        payload = [0x00, 99, 0x00, 3, 0x06,
                   0x00, 1, 0x00, 2, 0x00, 3]
        frame = build_frame(1, 0x10, payload)
        ms._process(frame)
        resp = cap.last()
        check("FC10 有应答", resp is not None)
        check("FC10 回显起始+数量",
              resp[2:6] == bytes([0x00, 99, 0x00, 3]), "resp=%r" % (resp,))
        check("FC10 delay_slots[0]=1", state.protect_params['delay_slots'][0] == 1)
        check("FC10 delay_slots[1]=2", state.protect_params['delay_slots'][1] == 2)
        check("FC10 delay_slots[2]=3", state.protect_params['delay_slots'][2] == 3)
    _with_capture(body)


def test_broadcast():
    def body(cap):
        reset_state()
        frame = build_frame(0, 0x06, [0x00, 0, 0x00, 0x3C])   # 广播 hr0=60
        ms._process(frame)
        check("广播写：无应答", len(cap.frames) == 0)
        check("广播写：state 已生效",
              abs(state.control_params['Kp'] - 0.6) < 1e-6)

        # 广播读不应答
        frame = build_frame(0, 0x03, [0x00, 0, 0x00, 1])
        ms._process(frame)
        check("广播读：无应答", len(cap.frames) == 0)
    _with_capture(body)


def test_bad_crc():
    def body(cap):
        reset_state()
        state.last_modbus_frame_ms = 111
        frame = bytearray(build_frame(1, 0x06, [0x00, 0, 0x00, 0x3C]))
        frame[-1] ^= 0xFF   # 破坏 CRC
        ms._process(bytes(frame))
        check("坏 CRC：无应答", len(cap.frames) == 0)
        check("坏 CRC：不更新时间戳", state.last_modbus_frame_ms == 111,
              "t=%s" % state.last_modbus_frame_ms)
        check("坏 CRC：state 未改变",
              abs(state.control_params['Kp'] - 0.6) < 1e-6)
    _with_capture(body)


def test_wrong_addr():
    def body(cap):
        reset_state()
        frame = build_frame(2, 0x06, [0x00, 0, 0x00, 0x3C])   # 从站地址 1
        ms._process(frame)
        check("非本机地址：无应答", len(cap.frames) == 0)
        check("非本机地址：state 未改变",
              abs(state.control_params['Kp'] - 0.6) < 1e-6)
    _with_capture(body)


def test_unsupported_fc():
    def body(cap):
        reset_state()
        frame = build_frame(1, 0x07, [0x00, 0x00, 0x00, 0x00])
        ms._process(frame)
        resp = cap.last()
        check("不支持功能码返回异常 0x01",
              resp is not None and resp[1] == (0x07 | 0x80) and resp[2] == 0x01,
              "resp=%r" % (resp,))
    _with_capture(body)


def test_manual_exv_and_coils():
    """0x1 系统使能 / 0x2 手动模式 / 4x223 EXV 手动开度（阀动测试软件侧）"""
    def body(cap):
        reset_state()
        # FC05 写线圈 0（系统使能）= ON
        ms._process(build_frame(1, 0x05, [0x00, 0, 0xFF, 0x00]))
        check("FC05 coil0=ON → ST_SYSTEM_ENABLE=1",
              state.state_data[state.ST_SYSTEM_ENABLE] == 1)
        # FC05 写线圈 1（手动使能）= ON
        ms._process(build_frame(1, 0x05, [0x00, 1, 0xFF, 0x00]))
        check("FC05 coil1=ON → MANUAL_MODE_ENABLE=1",
              state.manual_params['MANUAL_MODE_ENABLE'] == 1)
        # FC06 写 4x223 EXV 手动开度 = 450 → 45.0%
        ms._process(build_frame(1, 0x06, [0x00, 222, 0x01, 0xC2]))
        check("hr222 → MANUAL_EXV_PCT=45.0",
              abs(state.manual_params['MANUAL_EXV_PCT'] - 45.0) < 1e-6,
              "v=%s" % state.manual_params['MANUAL_EXV_PCT'])
    _with_capture(body)


# ═══════════════════════════════════════════════════════════
# 4. Dead code mark-only + host script presence
# ═══════════════════════════════════════════════════════════

def test_dead_code_marked_not_deleted():
    check("HAL/PWM.py 仍存在（仅标记）", file_exists("HAL/PWM.py"))
    check("Sensor/tem_lastV02_STM32.py 仍存在（仅标记）",
          file_exists("Sensor/tem_lastV02_STM32.py"))
    struct = read_file("docs/STRUCTURE.md")
    check("STRUCTURE 标注 PWM.py 死代码", "PWM.py" in struct and "死代码" in struct)
    check("STRUCTURE 标注 tem_lastV02 死代码", "tem_lastV02_STM32.py" in struct)


def test_pymodbus_script_present():
    check("pymodbus 上板脚本存在", file_exists("test/test_step7_pymodbus_host.py"))


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Step7 Verification Checklist (offline)")
    print("=" * 60)

    print("\n--- Rename (D3) ---")
    test_rename()

    print("\n--- A8 sentinel ---")
    test_a8_sentinel()

    print("\n--- Modbus protocol (mock UART) ---")
    test_fc01_read_coils()
    test_fc03_read_holding()
    test_fc04_read_input()
    test_fc02_read_discrete()
    test_fc05_write_coil()
    test_fc06_write_single()
    test_fc06_range_reject()
    test_fc0f_write_coils()
    test_fc10_write_multiple()
    test_broadcast()
    test_bad_crc()
    test_wrong_addr()
    test_unsupported_fc()
    test_manual_exv_and_coils()

    print("\n--- Dead code / host script ---")
    test_dead_code_marked_not_deleted()
    test_pymodbus_script_present()

    print("\n" + "=" * 60)
    print("Result: %d total, %d passed, %d failed" % (
        _test_count, _pass_count, _fail_count))
    print("=" * 60)

    sys.exit(0 if _fail_count == 0 else 1)

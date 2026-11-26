"""
Board/hmi_uart.py 中纯格式化函数的测试。

由于 hmi_uart.py 依赖 MicroPython 模块，本测试通过 sys.modules 注入 mock，
然后导入并验证格式化输出。
"""

import sys
import types
from io import BytesIO

# ── Mock MicroPython 模块 ──
mock_uasyncio = types.ModuleType("uasyncio")
mock_uasyncio.sleep_ms = lambda ms: None
sys.modules["uasyncio"] = mock_uasyncio

mock_utime = types.ModuleType("utime")
mock_utime.ticks_ms = lambda: 0
mock_utime.ticks_diff = lambda a, b: a - b
sys.modules["utime"] = mock_utime

mock_time = types.ModuleType("time")
mock_time.ticks_ms = lambda: 0
mock_time.ticks_diff = lambda a, b: a - b
mock_time.time = lambda: 0
sys.modules["time"] = mock_time

mock_pyb = types.ModuleType("pyb")
class MockUART:
    def __init__(self, *args, **kwargs):
        self.buf = BytesIO()
        self._any = 0
    def any(self):
        return self._any
    def read(self, n):
        return b""
    def write(self, data):
        self.buf.write(data)
class MockSPI:
    def __init__(self, *args, **kwargs): pass
class MockADC:
    def __init__(self, *args, **kwargs): pass
mock_pyb.UART = MockUART
mock_pyb.SPI = MockSPI
mock_pyb.ADC = MockADC
mock_pyb.Pin = lambda name: name
sys.modules["pyb"] = mock_pyb

# Mock machine
mock_machine = types.ModuleType("machine")
mock_machine.SoftSPI = object
sys.modules["machine"] = mock_machine

# Mock adc_direct
mock_adc_direct = types.ModuleType("adc_direct")
mock_adc_direct.read_pc2_c = lambda: 0
mock_adc_direct.read_pc3_c = lambda: 0
sys.modules["adc_direct"] = mock_adc_direct

# 现在导入 Board 模块
sys.path.insert(0, "/Users/zhi/Project/Refrigerate_System")

# 先 mock Board.board，避免拉入 pyb/machine/SPI 整个硬件初始化链
mock_board_mod = types.ModuleType("Board.board")
class MockUART:
    def __init__(self, *args, **kwargs):
        self.buf = BytesIO()
        self._any = 0
    def any(self): return self._any
    def read(self, n): return b""
    def write(self, data): self.buf.write(data)
mock_board_mod.HMI_UART = MockUART()
sys.modules["Board.board"] = mock_board_mod

from Board import hmi_uart as hu
import Board.state as state


def test_append_int():
    buf = bytearray(20)
    assert hu._append_int(buf, 0, 123) == 3
    assert buf[:3] == b"123"
    assert hu._append_int(buf, 0, -45) == 3
    assert buf[:3] == b"-45"
    assert hu._append_int(buf, 0, 0) == 1
    assert buf[:1] == b"0"
    print("_append_int OK")


def test_append_float():
    buf = bytearray(20)
    assert hu._append_float(buf, 0, 12.34) == 5
    assert buf[:5] == b"12.34"
    assert hu._append_float(buf, 0, -5.0) == 5
    assert buf[:5] == b"-5.00"
    assert hu._append_float(buf, 0, 0.0) == 4
    assert buf[:4] == b"0.00"
    print("_append_float OK")


def test_build_range_line():
    line = hu._build_range_line("SH_SETPOINT", 3.0, 10.0)
    assert line == b"R SH_SETPOINT 3.00 10.00\n", line
    line = hu._build_range_line("CAP_THROTTLE_MIN", 48, 2047)
    assert line == b"R CAP_THROTTLE_MIN 48 2047\n", line
    line = hu._build_range_line("SH_SETPOINT", None, None)
    assert line == b"R SH_SETPOINT - -\n", line
    print("_build_range_line OK")


def test_validate_value():
    ok, val = hu._validate_value("SH_SETPOINT", "8.0")
    assert ok and val == 8.0
    ok, code = hu._validate_value("CAP_THROTTLE_MIN", "48.5")
    assert not ok and code == "FORMAT"
    ok, code = hu._validate_value("SH_AGGR_MODE", "3")
    assert ok and code == 3
    print("_validate_value OK")


def test_crc():
    body = b"W SYSTEM_ENABLE 0"
    crc = hu._crc_xor(body)
    assert "{:02X}".format(crc) == "3C"
    print("_crc_xor OK")


def test_handle_command_handshake():
    hu._response_head = 0
    hu._response_tail = 0
    hu._handle_command("H")
    buf, ln = hu._dequeue_response()
    assert buf is not None
    resp = bytes(buf[:ln]).decode().strip()
    assert resp.startswith("H 4 75 134 ")
    print("handshake OK")


def test_handle_command_write_system_enable():
    hu._response_head = 0
    hu._response_tail = 0
    # W SYSTEM_ENABLE 0*3C
    hu._handle_command("W SYSTEM_ENABLE 0*3C")
    buf, ln = hu._dequeue_response()
    resp = bytes(buf[:ln]).decode().strip()
    assert resp == "OK"
    assert state.state_data[state.ST_SYSTEM_ENABLE] == 0
    print("write SYSTEM_ENABLE OK")


def test_handle_command_bad_crc():
    hu._response_head = 0
    hu._response_tail = 0
    hu._handle_command("W SYSTEM_ENABLE 0*00")
    buf, ln = hu._dequeue_response()
    resp = bytes(buf[:ln]).decode().strip()
    assert resp == "E CRC"
    print("bad CRC OK")


def test_handle_command_range_set():
    hu._response_head = 0
    hu._response_tail = 0
    # R SH_SETPOINT 3.0 10.0*XX
    import Board.params_store as ps
    body = "R SH_SETPOINT 3.0 10.0"
    crc = "{:02X}".format(hu._crc_xor(body.encode()))
    hu._handle_command("{}*{}".format(body, crc))
    buf, ln = hu._dequeue_response()
    resp = bytes(buf[:ln]).decode().strip()
    assert resp == "OK", resp
    rmin, rmax = ps.get_range("SH_SETPOINT")
    assert rmin == 3.0 and rmax == 10.0
    print("range set OK")


def test_handle_command_range_all():
    hu._response_head = 0
    hu._response_tail = 0
    hu._data_head = 0
    hu._data_tail = 0
    hu._data_slot = [None, None]
    hu._data_len = [0, 0]
    hu._data_iter = [None, None]

    hu._handle_command("R *")
    assert hu._response_head == hu._response_tail, "R * must not return E CRC"
    assert isinstance(hu._data_slot[0], tuple)
    assert hu._data_slot[0][0] == "ranges"
    print("range list request OK")


def test_handle_command_action():
    hu._response_head = 0
    hu._response_tail = 0
    body = "A FAULT_RESET"
    crc = "{:02X}".format(hu._crc_xor(body.encode()))
    hu._handle_command("{}*{}".format(body, crc))
    buf, ln = hu._dequeue_response()
    resp = bytes(buf[:ln]).decode().strip()
    assert resp == "OK"
    assert state.fault_reset_request is True
    print("action OK")


def test_handle_command_write_param():
    hu._response_head = 0
    hu._response_tail = 0
    body = "W SH_SETPOINT 8.5"
    crc = "{:02X}".format(hu._crc_xor(body.encode()))
    hu._handle_command("{}*{}".format(body, crc))
    buf, ln = hu._dequeue_response()
    resp = bytes(buf[:ln]).decode().strip()
    assert resp == "OK", resp
    assert state.control_params["setpoint"] == 8.5
    print("write param OK")


def test_snapshot_format():
    # 设置一些状态值
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_data[state.IDX_SUCTION_T] = 12.34
    ln = hu._build_dynamic_snapshot(5)
    line = bytes(hu._dyn_buf[:ln]).decode()
    assert line.startswith("S 5 ")
    assert "12.34" in line
    print("snapshot format OK")


def test_param_snapshot_reads_parameters():
    state.control_params["Kp"] = 0.6
    state.control_params["Ki"] = 0.12
    state.control_params["Kd"] = 3.0
    state.control_params["setpoint"] = 8.5

    ln = hu._build_param_snapshot()
    fields = bytes(hu._param_buf[:ln]).decode().strip().split()
    assert fields[0] == "Q"
    assert fields[1:5] == ["0.60", "0.12", "3.00", "8.50"], fields[:6]
    print("parameter snapshot mapping OK")


if __name__ == "__main__":
    test_append_int()
    test_append_float()
    test_build_range_line()
    test_validate_value()
    test_crc()
    test_handle_command_handshake()
    test_handle_command_write_system_enable()
    test_handle_command_bad_crc()
    test_handle_command_range_set()
    test_handle_command_range_all()
    test_handle_command_action()
    test_handle_command_write_param()
    test_snapshot_format()
    test_param_snapshot_reads_parameters()
    print("全部通过")

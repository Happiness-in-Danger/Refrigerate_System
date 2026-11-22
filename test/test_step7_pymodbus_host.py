#!/usr/bin/env python3
"""
Step7 上板 Modbus 总线总测（pymodbus 主机脚本）

依据：PLAN.md §2 Step7 实测 DoD / STEP6_SEAL.md §6.1
覆盖：FC01 / FC02 / FC03 / FC04 / FC05 / FC06 / FC10、广播、坏 CRC、
      异常码、4x223 手动 EXV 阀动（需 --valve，且必须脱开阀体）、999/di/ir 抽检。

运行环境（主机，非 H743）：
    pip install pymodbus pyserial
    python3 test/test_step7_pymodbus_host.py --port /dev/ttyUSB0
    python3 test/test_step7_pymodbus_host.py --port COM3 --baud 9600

安全警告：
    --valve 会真实驱动 EXV 电机。执行前必须把阀体从电机上脱开，
    否则可能损坏阀体或丢失机械零点。

掉电保持（Flash）验证为两阶段，需人工断电：
    python3 test/test_step7_pymodbus_host.py --port /dev/ttyUSB0 --persist-write
    # 人工给 H743 断电再上电
    python3 test/test_step7_pymodbus_host.py --port /dev/ttyUSB0 --persist-verify

参考寄存器映射：docs/参考.csv
"""

import argparse
import sys
import time

try:
    from pymodbus.client import ModbusSerialClient
except Exception:
    try:
        from pymodbus.client.sync import ModbusSerialClient
    except Exception:
        print("缺少 pymodbus，请先执行: pip install pymodbus pyserial")
        sys.exit(2)


# ─── Modbus RTU 工具 ────────────────────────────────────────
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def build_frame(addr: int, fc: int, payload) -> bytes:
    body = bytes([addr, fc]) + bytes(payload)
    return body + crc16(body).to_bytes(2, "little")


def check_frame_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    return crc16(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


# ─── 结果统计 ───────────────────────────────────────────────
class Report:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        self.total += 1
        if ok:
            self.passed += 1
            print("[PASS] %s" % name)
        else:
            self.failed += 1
            print("[FAIL] %s%s" % (name, (" — " + detail) if detail else ""))


REPORT = Report()


# ─── pymodbus 版本兼容调用 ──────────────────────────────────
def call(client, method, *args, **kwargs):
    """兼容 pymodbus 3.x 的 slave= / device_id= 参数差异。"""
    try:
        return method(*args, slave=SLAVE, **kwargs)
    except TypeError:
        return method(*args, device_id=SLAVE, **kwargs)


def read_holding(client, address, count):
    return call(client, client.read_holding_registers, address, count=count)


def read_input(client, address, count):
    return call(client, client.read_input_registers, address, count=count)


def read_discrete(client, address, count):
    return call(client, client.read_discrete_inputs, address, count=count)


def read_coils(client, address, count):
    return call(client, client.read_coils, address, count=count)


def write_coil(client, address, value):
    return call(client, client.write_coil, address, value)


def write_single(client, address, value):
    return call(client, client.write_register, address, value)


def write_multiple(client, address, values):
    return call(client, client.write_registers, address, values)


def is_error(resp):
    return resp is None or (hasattr(resp, "isError") and resp.isError())


# ─── 原始串口（广播 / 坏 CRC）──────────────────────────────
def get_raw_serial(client):
    """取 pymodbus 底层串口对象，用于发送不应答的原始帧。"""
    for attr in ("socket", "transport", "_client"):
        obj = getattr(client, attr, None)
        if obj is not None and hasattr(obj, "write") and hasattr(obj, "read"):
            return obj
    return None


def raw_no_response(ser, frame, wait_s=0.5):
    """发送原始帧，返回 (收到字节数, 原始响应)。"""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    ser.write(frame)
    ser.flush()
    time.sleep(wait_s)
    try:
        n = ser.in_waiting
    except Exception:
        n = 0
    data = ser.read(n or 1)
    return len(data), data


# ─── 测试项 ─────────────────────────────────────────────────
SLAVE = 1


def test_fc01_coils(client):
    print("\n--- FC01 读线圈 ---")
    resp = read_coils(client, 0, 8)
    if is_error(resp):
        REPORT.check("FC01 coil0..coil7", False, repr(resp))
        return
    REPORT.check("FC01 coil0..coil7 (%d bits)" % len(resp.bits), True)
    ok = all(b in (0, 1, False, True) for b in resp.bits)
    REPORT.check("coil 位图取值合法", ok)


def test_fc03_holding(client):
    print("\n--- FC03 读保持寄存器 ---")
    for start, count in ((0, 7), (15, 9), (30, 4), (49, 13),
                         (99, 8), (149, 8), (179, 7), (199, 6)):
        resp = read_holding(client, start, count)
        if is_error(resp):
            REPORT.check("FC03 hr%d..hr%d" % (start, start + count - 1), False,
                         repr(resp))
            return
        REPORT.check("FC03 hr%d..hr%d (%d regs)" % (
            start, start + count - 1, len(resp.registers)), True)


def test_fc04_input(client):
    print("\n--- FC04 读输入寄存器 ---")
    resp = read_input(client, 0, 17)
    if is_error(resp):
        REPORT.check("FC04 ir0..ir16", False, repr(resp))
        return
    REPORT.check("FC04 ir0..ir16 (%d regs)" % len(resp.registers), True)
    ir = resp.registers
    REPORT.check("ir13 电流 999 哨兵或有效值", ir[13] == 999 or 0 <= ir[13] <= 5000,
                 "ir13=%d" % ir[13])
    resp2 = read_input(client, 99, 3)
    if not is_error(resp2):
        REPORT.check("ir99 sys_mode 在 0-7", 0 <= resp2.registers[0] <= 7,
                     "ir99=%d" % resp2.registers[0])
    else:
        REPORT.check("FC04 ir99..ir101", False, repr(resp2))


def test_fc02_discrete(client):
    print("\n--- FC02 读离散量输入 ---")
    resp = read_discrete(client, 0, 16)
    if is_error(resp):
        REPORT.check("FC02 di0..di15", False, repr(resp))
        return
    REPORT.check("FC02 di0..di15 (%d bits)" % len(resp.bits), True)
    bits = resp.bits
    ok = all(b in (0, 1, False, True) for b in bits)
    REPORT.check("di 位图取值合法", ok)
    resp2 = read_discrete(client, 99, 6)
    REPORT.check("FC02 di99..di104", not is_error(resp2), repr(resp2))


def test_fc05_coil(client):
    print("\n--- FC05 写单个线圈 ---")
    # coil1 手动模式：读回、切换、恢复
    cur = read_coils(client, 1, 1)
    origin = cur.bits[0] if not is_error(cur) else False
    try:
        w = write_coil(client, 1, not origin)
        REPORT.check("FC05 写 coil1 成功", not is_error(w), repr(w))
        rb = read_coils(client, 1, 1)
        REPORT.check("FC05 回读 coil1 切换",
                     not is_error(rb) and rb.bits[0] == (not origin), repr(rb))
    finally:
        write_coil(client, 1, origin)


def test_fc06_single(client):
    print("\n--- FC06 写单个保持寄存器 ---")
    # hr0 = 过热度环 Kp（×100），先读回、测完恢复
    cur = read_holding(client, 0, 1)
    origin = cur.registers[0] if not is_error(cur) else 60
    try:
        w = write_single(client, 0, 60)
        REPORT.check("FC06 写 hr0=60 成功", not is_error(w), repr(w))
        rb = read_holding(client, 0, 1)
        REPORT.check("FC06 回读 hr0=60",
                     not is_error(rb) and rb.registers[0] == 60, repr(rb))
        # 越界写入 hr31 应返回异常码
        bad = write_single(client, 31, 9999)
        REPORT.check("FC06 越界写 hr31 返回异常", is_error(bad), repr(bad))
    finally:
        write_single(client, 0, origin)


def test_fc10_multiple(client):
    print("\n--- FC10 写多个保持寄存器 ---")
    # 4x100-4x102 延时槽 0-2
    orig = read_holding(client, 99, 3)
    origin = orig.registers if not is_error(orig) else [0, 5, 10]
    try:
        w = write_multiple(client, 99, [1, 2, 3])
        REPORT.check("FC10 写 hr99-101=[1,2,3] 成功", not is_error(w), repr(w))
        rb = read_holding(client, 99, 3)
        REPORT.check("FC10 回读 hr99-101=[1,2,3]",
                     not is_error(rb) and list(rb.registers) == [1, 2, 3],
                     repr(rb))
    finally:
        write_multiple(client, 99, origin)


def test_broadcast(client):
    print("\n--- 广播地址（addr=0，只写不应答）---")
    ser = get_raw_serial(client)
    if ser is None:
        print("[SKIP] 无法访问底层串口，跳过广播/坏 CRC 原始帧测试")
        return
    # 广播写 hr0=60（过热度环 Kp=0.6，安全值）
    frame = build_frame(0, 0x06, [0x00, 0x00, 0x00, 0x3C])
    n, data = raw_no_response(ser, frame)
    REPORT.check("广播写无应答", n == 0, "收到 %d 字节: %r" % (n, data))
    rb = read_holding(client, 0, 1)
    REPORT.check("广播写已生效（hr0=60）",
                 not is_error(rb) and rb.registers[0] == 60, repr(rb))


def test_bad_crc(client):
    print("\n--- 坏 CRC 帧（应被丢弃）---")
    ser = get_raw_serial(client)
    if ser is None:
        return
    frame = bytearray(build_frame(SLAVE, 0x06, [0x00, 0x00, 0x00, 0x01]))
    frame[-1] ^= 0xFF
    n, data = raw_no_response(ser, bytes(frame))
    REPORT.check("坏 CRC 无应答", n == 0, "收到 %d 字节: %r" % (n, data))


def test_valve(client):
    print("\n--- 4x223 手动 EXV 阀动（必须脱开阀体）---")
    print("!! 请确认阀体已脱开 !!")
    time.sleep(2)
    pos0 = read_input(client, 9, 1)   # 3x10 EXV 当前开度
    p0 = pos0.registers[0] if not is_error(pos0) else None
    REPORT.check("读取 ir9 初始阀位", p0 is not None, repr(pos0))
    try:
        w1 = write_coil(client, 1, True)     # 0x2 手动使能
        REPORT.check("写 coil1=ON 手动使能", not is_error(w1), repr(w1))
        w2 = write_single(client, 222, 500)  # 4x223 EXV 50.0%
        REPORT.check("写 hr222=500 (50.0%)", not is_error(w2), repr(w2))
        moved = False
        for _ in range(20):
            time.sleep(0.5)
            rb = read_input(client, 9, 1)
            if not is_error(rb) and abs(rb.registers[0] - 500) <= 20:
                moved = True
                break
        REPORT.check("ir9 阀位跟随至 50.0%±2.0%", moved)
        w3 = write_single(client, 222, 300)  # 回 30%
        REPORT.check("写 hr222=300 (30.0%) 复位", not is_error(w3), repr(w3))
    finally:
        write_coil(client, 1, False)         # 退出手动
        write_single(client, 222, 300)


# ─── 掉电保持两阶段 ─────────────────────────────────────────
PERSIST_HR = 99      # 4x100 延时槽 0，配置类，会被持久化
PERSIST_VAL = 7


def test_persist_write(client):
    print("\n--- 掉电保持：写入阶段 ---")
    w = write_single(client, PERSIST_HR, PERSIST_VAL)
    REPORT.check("写入 hr%d=%d" % (PERSIST_HR, PERSIST_VAL), not is_error(w),
                 repr(w))
    print("等待 35s 让 30s 防抖落盘...")
    time.sleep(35)
    rb = read_holding(client, PERSIST_HR, 1)
    REPORT.check("回读 hr%d=%d" % (PERSIST_HR, PERSIST_VAL),
                 not is_error(rb) and rb.registers[0] == PERSIST_VAL, repr(rb))
    print("请给 H743 断电再上电，然后运行 --persist-verify")


def test_persist_verify(client):
    print("\n--- 掉电保持：验证阶段 ---")
    rb = read_holding(client, PERSIST_HR, 1)
    ok = not is_error(rb) and rb.registers[0] == PERSIST_VAL
    REPORT.check("重启后 hr%d 仍为 %d（掉电保持）" % (PERSIST_HR, PERSIST_VAL),
                 ok, repr(rb))
    if ok:
        print("掉电保持验证通过。建议恢复 hr%d 原值。" % PERSIST_HR)


# ─── main ───────────────────────────────────────────────────
def main():
    global SLAVE
    ap = argparse.ArgumentParser(description="Step7 Modbus 总线总测（pymodbus 主机）")
    ap.add_argument("--port", required=True, help="串口，如 /dev/ttyUSB0 或 COM3")
    ap.add_argument("--baud", type=int, default=9600, help="波特率（默认 9600）")
    ap.add_argument("--slave", type=int, default=1, help="从站地址（默认 1）")
    ap.add_argument("--timeout", type=float, default=1.0, help="应答超时秒")
    ap.add_argument("--valve", action="store_true",
                    help="执行 4x223 阀动测试（必须先脱开阀体）")
    ap.add_argument("--persist-write", action="store_true",
                    help="掉电保持写入阶段（等待 30s 落盘）")
    ap.add_argument("--persist-verify", action="store_true",
                    help="掉电保持验证阶段（断电重启后运行）")
    args = ap.parse_args()
    SLAVE = args.slave

    client = ModbusSerialClient(
        port=args.port, baudrate=args.baud, bytesize=8, parity="N",
        stopbits=1, timeout=args.timeout,
    )
    if not client.connect():
        print("无法打开串口 %s" % args.port)
        return 2
    print("已连接 %s @ %d，从站地址 %d" % (args.port, args.baud, SLAVE))

    try:
        if args.persist_verify:
            test_persist_verify(client)
        elif args.persist_write:
            test_persist_write(client)
        else:
            test_fc01_coils(client)
            test_fc03_holding(client)
            test_fc04_input(client)
            test_fc02_discrete(client)
            test_fc05_coil(client)
            test_fc06_single(client)
            test_fc10_multiple(client)
            test_broadcast(client)
            test_bad_crc(client)
            if args.valve:
                test_valve(client)
            else:
                print("\n[SKIP] 阀动测试（加 --valve，且先脱开阀体）")
    finally:
        client.close()

    print("\n" + "=" * 60)
    print("Result: %d total, %d passed, %d failed" % (
        REPORT.total, REPORT.passed, REPORT.failed))
    print("=" * 60)
    return 0 if REPORT.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

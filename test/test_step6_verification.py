#!/usr/bin/env python3
"""
Step6 Verification Checklist

Step6 DoD（依据 PLAN.md §2 / DESIGN.md §9-§10 / docs/参考.csv）：
1. 参数单源：state.py 参数字典为内存唯一来源
2. 寄存器增补落地：coils0-7 / 4x1-223 / 3x1-223 / 1x1-116 全量映射（基准 docs/参考.csv）
3. 范围校验 + 分级阈值一致性校验（越界/违反返回 0x03）
4. 延时槽 hr50-57 可读写
5. ESC 遥测 ir33-48 映射
6. B6 周期失配重标（FRESHNESS_MS_GLOBAL=3400，RATE_LIMITS ×4，单源）
7. C8 传感器故障多 bit 映射（ir12 完整 sensor_fault_reg）
8. A6 PID_Plus save/load MicroPython 兼容
9. 参数持久化（DESIGN §9.4）：掉电保持 / checksum / 损坏回退 / 30s 防抖
10. 审计遗留项：FAN_RPM_TOL 接入、容量环周期 period_s 可配

本脚本只做静态检查和可 mock 的单元测试，不连接硬件。
"""

import sys
import os
import time as _time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ═══════════════════════════════════════════════════════════
# Mock MicroPython modules
# ═══════════════════════════════════════════════════════════
_start_ticks = int(_time.time() * 1000)
_mock_now = [_start_ticks]


def ticks_ms():
    return _mock_now[0]


def ticks_diff(a, b):
    diff = (a - b) & 0x3FFFFFFF
    if diff > 0x1FFFFFFF:
        diff -= 0x40000000
    return diff


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
import Board.params_store as params_store
from Display_Touch import modbus_slave as ms
from Display_Touch.modbus_regs import regs
from Sensor import validate as validate_mod
from Alarm import supervisor

# 参数默认值快照（用于测试间复位）
_DEFAULTS = {
    "control_params": dict(state.control_params),
    "compressor_control_params": dict(state.compressor_control_params),
    "cond_control_params": dict(state.cond_control_params),
    "comp_speed_loop_params": dict(state.comp_speed_loop_params),
    "fan_speed_loop_params": dict(state.fan_speed_loop_params),
    "protect_params": dict(state.protect_params),
}

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


def reset_state():
    state.sys_mode = state.MODE_SAFE_STOP
    state.error_reg = 0xFFFFFFFF
    state.sensor_fault_reg = 0xFFFF
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    state.fault_reset_request = False
    state.exv_target_pct = 0.0
    state.comp_throttle_out = 0
    state.evap_fan_throttle_out = 0
    state.cond_fan_throttle_out = 0
    state.startup_stage = 0
    state.limit_actions = 0
    state.retry_lockout = False
    state.subcooling = 999
    state.liquid_enthalpy = 999
    for i in range(len(regs.holding_regs)):
        regs.holding_regs[i] = 0
    for i in range(len(regs.input_regs)):
        regs.input_regs[i] = 0
    for i in range(len(regs.coils)):
        regs.coils[i] = 0
    for i in range(len(regs.discrete_inputs)):
        regs.discrete_inputs[i] = 0
    # 恢复参数字典默认值
    for attr, defaults in _DEFAULTS.items():
        target = getattr(state, attr)
        for k, v in defaults.items():
            target[k] = list(v) if isinstance(v, list) else v
    params_store._dirty = False
    params_store._dirty_ms = 0
    params_store._write_count = 0


# ═══════════════════════════════════════════════════════════
# Static checks
# ═══════════════════════════════════════════════════════════

def test_register_sizes():
    check("coils 长度 8 (0x1-0x8)", len(regs.coils) == 8,
          "len=%d" % len(regs.coils))
    check("holding_regs 长度 223 (4x1-4x223)", len(regs.holding_regs) == 223,
          "len=%d" % len(regs.holding_regs))
    check("input_regs 长度 223 (3x1-3x223)", len(regs.input_regs) == 223,
          "len=%d" % len(regs.input_regs))
    check("discrete_inputs 长度 116 (1x1-1x116)", len(regs.discrete_inputs) == 116,
          "len=%d" % len(regs.discrete_inputs))


def test_b6_recalibration():
    check("FRESHNESS_MS_GLOBAL == 3400",
          validate_mod.FRESHNESS_MS_GLOBAL == 3400,
          str(getattr(validate_mod, "FRESHNESS_MS_GLOBAL", None)))
    check("FRESHNESS_MS 兼容引用同一常量",
          validate_mod.FRESHNESS_MS == validate_mod.FRESHNESS_MS_GLOBAL)
    check("RATE_LIMITS 温度 ×4 (20.0)",
          validate_mod.RATE_LIMITS[state.IDX_SUCTION_T] == 20.0)
    check("RATE_LIMITS 压力 ×4 (12.0)",
          validate_mod.RATE_LIMITS[state.IDX_DISCHARGE_PSI] == 12.0)
    check("RATE_LIMITS 电流 ×4 (80.0)",
          validate_mod.RATE_LIMITS[state.IDX_CURRENT] == 80.0)
    src = read_file("EXV/Valve_CRTL.py")
    check("Valve_CRTL 引用 FRESHNESS_MS_GLOBAL（D-8 单源）",
          "FRESHNESS_MS_GLOBAL" in src and "sh_age > 1000" not in src)


def test_pid_plus_micropython():
    src = read_file("HAL/PID_Plus.py")
    check("PID_Plus 无 encoding= 参数", "encoding=" not in src)
    check("PID_Plus 无 indent= 参数", "indent=" not in src)
    check("PID_Plus 无 ensure_ascii= 参数", "ensure_ascii=" not in src)


def test_speed_loop_enable_explicit():
    """MINOR-1: 三个转速环使能开关必须被设备模块显式消费"""
    mapping = {
        "Compressor/control.py": "COMP_SPEED_LOOP_ENABLE",
        "Condenser/control.py": "COND_FAN_SPEED_LOOP_ENABLE",
        "Evaporator/control.py": "EVAP_FAN_SPEED_LOOP_ENABLE",
    }
    for fpath, key in mapping.items():
        src = read_file(fpath)
        check("%s 显式消费 %s" % (fpath, key), key in src)
        check("%s 使能关闭时跳过转速 PID" % fpath,
              "not speed_loop_enabled" in src)


def test_audit_leftovers():
    evap = read_file("Evaporator/control.py")
    cond = read_file("Condenser/control.py")
    check("FAN_RPM_TOL 接入蒸发风机故障检测", "FAN_RPM_TOL" in evap)
    check("FAN_RPM_TOL 接入冷凝风机故障检测", "FAN_RPM_TOL" in cond)
    comp = read_file("Compressor/control.py")
    check("容量环周期 period_s 可配", "period_s" in comp)


# ═══════════════════════════════════════════════════════════
# Holding register mapping tests
# ═══════════════════════════════════════════════════════════

def test_hr_write_mapping():
    reset_state()
    # 4x16 容量环 Kp -> hr15
    code = ms._write_and_apply(15, [5500])
    check("hr15 -> cap Kp=55.0", code == 0 and
          abs(state.compressor_control_params["Kp"] - 55.0) < 1e-6)
    # 4x31 高压停机 -> hr30
    code = ms._write_and_apply(30, [2150])
    check("hr30 -> HP_MAX=21.5", code == 0 and
          abs(state.protect_params["HP_MAX"] - 21.5) < 1e-6)
    # 4x101 延时槽 1 -> hr100
    code = ms._write_and_apply(100, [7])
    check("hr100 -> delay_slots[1]=7", code == 0 and
          state.protect_params["delay_slots"][1] == 7)
    # 4x120 EXV 启动开度 -> hr119
    code = ms._write_and_apply(119, [250])
    check("hr119 -> EXV_START_POS=25.0", code == 0 and
          abs(state.protect_params["EXV_START_POS"] - 25.0) < 1e-6)
    # 4x19 容量环设定温度 ×100 -> hr18
    code = ms._write_and_apply(18, [2350])
    check("hr18 -> cap setpoint=23.5", code == 0 and
          abs(state.compressor_control_params["setpoint"] - 23.5) < 1e-6)
    # 4x34 冷凝压力目标 -> hr33（同时写 protect 与 cond setpoint）
    code = ms._write_and_apply(33, [1300])
    check("hr33 -> HP_TARGET/cond setpoint=13.0", code == 0 and
          abs(state.protect_params["HP_TARGET"] - 13.0) < 1e-6 and
          abs(state.cond_control_params["setpoint"] - 13.0) < 1e-6)
    # 4x24 容量环周期 -> hr23
    code = ms._write_and_apply(23, [15])
    check("hr23 -> cap period_s=15", code == 0 and
          state.compressor_control_params["period_s"] == 15)


def test_speed_loop_mapping():
    reset_state()
    # 4x153 压缩机最大转速 -> hr152
    code = ms._write_and_apply(152, [5500])
    ok = (code == 0 and state.comp_speed_loop_params["rpm_max"] == 5500 and
          state.state_data[state.ST_MOTOR_SPEED_MAX] == 5500)
    check("hr152 -> comp rpm_max + ST_MOTOR_SPEED_MAX", ok)
    # 4x154 压缩机转速环 Kp -> hr153
    code = ms._write_and_apply(153, [75])
    check("hr153 -> comp speed Kp=0.75", code == 0 and
          abs(state.comp_speed_loop_params["Kp"] - 0.75) < 1e-6)
    # 4x180 风机最大转速 -> hr179
    code = ms._write_and_apply(179, [2500])
    ok = (code == 0 and state.fan_speed_loop_params["rpm_max"] == 2500 and
          state.state_data[state.ST_FAN_SPEED_MAX] == 2500)
    check("hr179 -> fan rpm_max + ST_FAN_SPEED_MAX", ok)
    # 4x184 风机最大调节量 -> hr183
    code = ms._write_and_apply(183, [120])
    check("hr183 -> fan throttle_max_adjust=120", code == 0 and
          state.fan_speed_loop_params["throttle_max_adjust"] == 120)


def test_range_validation():
    reset_state()
    code = ms._write_and_apply(30, [9999])   # > 2600
    check("越界写入 hr30 返回 0x03", code == 0x03, "code=%s" % code)
    check("越界写入不改变 HP_MAX",
          abs(state.protect_params["HP_MAX"] - 22.0) < 1e-6)
    code = ms._write_and_apply(48, [1])      # 4x49 未定义
    check("保留地址 hr48 返回 0x02", code == 0x02, "code=%s" % code)


def test_consistency_validation():
    reset_state()
    # 默认 HP_WARN=19, HP_UNLOAD=20.5, HP_MAX=22；4x33 -> hr32
    code = ms._write_and_apply(32, [2100])   # HP_WARN=21 > HP_UNLOAD
    check("一致性违反返回 0x03", code == 0x03, "code=%s" % code)
    check("一致性违反回滚 HP_WARN",
          abs(state.protect_params["HP_WARN"] - 19.0) < 1e-6)


def test_coil_write_sync():
    reset_state()
    code = ms._write_coils_and_apply(0, [1])
    check("coil0 -> ST_SYSTEM_ENABLE=1", code == 0 and
          state.state_data[state.ST_SYSTEM_ENABLE] == 1)
    code = ms._write_coils_and_apply(1, [1])
    check("coil1 -> MANUAL_MODE_ENABLE=1", code == 0 and
          state.manual_params['MANUAL_MODE_ENABLE'] == 1)
    code = ms._write_coils_and_apply(2, [0])
    check("coil2 -> COMP_SPEED_LOOP_ENABLE=0", code == 0 and
          state.protect_params["COMP_SPEED_LOOP_ENABLE"] == 0)
    code = ms._write_coils_and_apply(5, [1])
    check("coil5 -> FLOW_ENABLE=1", code == 0 and
          state.protect_params["FLOW_ENABLE"] == 1)
    code = ms._write_coils_and_apply(7, [0])
    check("coil7 -> STARTUP_ADVANCE_MODE=0", code == 0 and
          state.protect_params["STARTUP_ADVANCE_MODE"] == 0)
    code = ms._write_coils_and_apply(8, [1])
    check("越界线圈返回 0x02", code == 0x02, "code=%s" % code)


def test_coil_reset_pulse():
    reset_state()
    code = ms._write_coils_and_apply(6, [1])
    check("coil6 写 1 -> fault_reset_request", code == 0 and
          state.fault_reset_request is True)
    check("coil6 回读恒 0", regs.coils[6] == 0)


def test_coil_sync_from_state():
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.manual_params['MANUAL_MODE_ENABLE'] = 1
    state.protect_params['FLOW_ENABLE'] = 1
    ms.sync_coils_from_state()
    check("sync_coils_from_state 系统使能", regs.coils[0] == 1)
    check("sync_coils_from_state 手动模式", regs.coils[1] == 1)
    check("sync_coils_from_state 流量使能", regs.coils[5] == 1)


# ═══════════════════════════════════════════════════════════
# Input / discrete sync tests
# ═══════════════════════════════════════════════════════════

def test_input_regs_sync():
    reset_state()
    state.sensor_data[0] = 12.34
    state.sensor_data[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_data[state.IDX_DISCHARGE_PSI] = 15.0
    state.sensor_data[state.IDX_SUPERHEAT] = 6.0
    state.sensor_data[state.IDX_E_VALUE] = 420.5
    state.sensor_data[state.IDX_CURRENT] = 999
    state.subcooling = 4.5
    state.liquid_enthalpy = 258.3
    for i in range(10):
        state.sensor_quality[i] = state.Q_OK
    state.sensor_quality[0] = state.Q_OK
    state.sensor_quality[1] = state.Q_SUSPECT
    state.sensor_quality[8] = state.Q_BAD
    state.sensor_fault_reg = 0xFF7F
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_MODULATING
    state.error_reg = 0xFFFFFFFE   # bit0 异常
    state.state_data[state.ST_VALVE_POS] = 45.5
    state.exv_target_pct = 30.0
    state.comp_throttle_out = 123
    state.evap_fan_throttle_out = 456
    state.cond_fan_throttle_out = 789
    state.driver_link_ok = True
    state.state_data[state.ST_MOTOR_SPEED] = 3000
    state.state_data[state.ST_EVAP_FAN_RPM] = 1200
    state.state_data[state.ST_COND_FAN_RPM] = 1500
    state.supervisor_interlocks["comp_enable"] = True
    state.startup_stage = 3
    state.retry_lockout = True
    state.limit_actions = 0x05
    state.state_data[16] = 4321
    ms.sync_input_regs()
    ir = regs.input_regs
    check("ir0 sensor_data ×100", ir[0] == 1234)
    check("ir5 排气压力 ×100", ir[5] == 1500)
    check("ir6 吸气压力 ×100", ir[6] == 500)
    check("ir7 过热度 ×10", ir[7] == 60)
    check("ir8 过冷度 ×10", ir[8] == 45)
    check("ir9 进口焓 ×100", ir[9] == 42050)
    check("ir10 出口焓 ×100", ir[10] == 25830)
    check("ir11 EXV 开度 ×10", ir[11] == 455)
    check("ir12 EXV 目标 ×10", ir[12] == 300)
    check("ir13 整机电流 哨兵 999 透传", ir[13] == 999)
    check("ir14 质量码低 8 位", ir[14] == (0 | (1 << 2)))
    check("ir15 质量码高 2 位", ir[15] == 2)
    check("ir16 sensor_fault_reg (C8)", ir[16] == 0xFF7F)
    check("ir99 sys_mode", ir[99] == state.MODE_RUNNING)
    check("ir100 startup_stage", ir[100] == 3)
    check("ir101 running_substate", ir[101] == state.RUNNING_MODULATING)
    check("ir109 error_reg 低 16", ir[109] == 0xFFFE)
    check("ir110 error_reg 高 16", ir[110] == 0xFFFF)
    check("ir199 压缩机油门", ir[199] == 123)
    check("ir200 压缩机转速", ir[200] == 3000)
    check("ir201 蒸发风机油门", ir[201] == 456)
    check("ir202 蒸发风机转速", ir[202] == 1200)
    check("ir203 冷凝风机油门", ir[203] == 789)
    check("ir204 冷凝风机转速", ir[204] == 1500)
    check("ir206 ESC0 遥测", ir[206] == 4321)
    check("ir222 L1 限载动作码", ir[222] == 0x05)
    check("di0 error_reg bit0 镜像=0", regs.discrete_inputs[0] == 0)
    check("di1 error_reg bit1 镜像=1", regs.discrete_inputs[1] == 1)
    check("di15 error_reg bit15 镜像=1", regs.discrete_inputs[15] == 1)
    check("di99 Driver 链路", regs.discrete_inputs[99] == 1)
    check("di100 压缩机使能联锁", regs.discrete_inputs[100] == 1)
    check("di104 retry_lockout", regs.discrete_inputs[104] == 1)


def test_holding_readback():
    reset_state()
    state.protect_params["HP_MAX"] = 21.0
    state.protect_params["delay_slots"][1] = 8
    state.compressor_control_params["period_s"] = 12.0
    state.manual_params['MANUAL_EXV_PCT'] = 33.3
    ms.sync_holding_readback()
    hr = regs.holding_regs
    check("回读 hr30 HP_MAX ×100", hr[30] == 2100)
    check("回读 hr100 延时槽 1", hr[100] == 8)
    check("回读 hr23 容量环周期", hr[23] == 12)
    check("回读 hr222 EXV 手动开度 ×10", hr[222] == 333)


# ═══════════════════════════════════════════════════════════
# Persistence tests
# ═══════════════════════════════════════════════════════════

def test_persistence_roundtrip():
    reset_state()
    tmp = os.path.join(tempfile.gettempdir(), "step6_cfg_roundtrip.json")
    try:
        os.remove(tmp)
    except Exception:
        pass
    state.protect_params["HP_MAX"] = 21.25
    state.control_params["Kp"] = 0.75
    params_store.mark_dirty()
    ok_save = params_store.save_now(tmp)
    # 改内存，再从文件恢复
    state.protect_params["HP_MAX"] = 22.0
    state.control_params["Kp"] = 0.6
    ok_load = params_store.load_config(tmp)
    check("参数保存成功", ok_save)
    check("参数加载成功", ok_load)
    check("HP_MAX 掉电保持", abs(state.protect_params["HP_MAX"] - 21.25) < 1e-6)
    check("control_params Kp 掉电保持", abs(state.control_params["Kp"] - 0.75) < 1e-6)
    os.remove(tmp)


def test_persistence_corruption():
    reset_state()
    tmp = os.path.join(tempfile.gettempdir(), "step6_cfg_bad.json")
    with open(tmp, "w") as f:
        f.write("{ this is not valid json ")
    ok = params_store.load_config(tmp)
    check("损坏配置回退默认值（返回 False）", ok is False)
    os.remove(tmp)

    # checksum 不匹配
    tmp2 = os.path.join(tempfile.gettempdir(), "step6_cfg_cksum.json")
    with open(tmp2, "w") as f:
        f.write('{"version":1,"checksum":1234,"params":{"protect":{"HP_MAX":9.9}}}')
    ok2 = params_store.load_config(tmp2)
    check("checksum 不匹配回退默认值", ok2 is False)
    os.remove(tmp2)


def test_persistence_debounce():
    reset_state()
    tmp = os.path.join(tempfile.gettempdir(), "step6_cfg_debounce.json")
    try:
        os.remove(tmp)
    except Exception:
        pass
    old_path = params_store.CONFIG_PATH
    params_store.CONFIG_PATH = tmp
    try:
        params_store.mark_dirty()
        params_store.tick(_mock_now[0] + 1000)   # 未到 30s
        not_yet = not os.path.exists(tmp)
        params_store.tick(_mock_now[0] + 31000)  # 到点
        saved = os.path.exists(tmp)
        check("30s 防抖：未到点不写 flash", not_yet)
        check("30s 防抖：到点落盘", saved)
        check("写次数计数=1", params_store.write_count() == 1)
    finally:
        params_store.CONFIG_PATH = old_path
        try:
            os.remove(tmp)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# Startup stage / S5
# ═══════════════════════════════════════════════════════════

def test_startup_stage_mirror():
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    supervisor.tick()   # SAFE_STOP -> STARTUP
    check("supervisor 进入 STARTUP", state.sys_mode == state.MODE_STARTUP)
    supervisor.tick()   # S1
    check("startup_stage 镜像已刷新", state.startup_stage == 1,
          "stage=%d" % state.startup_stage)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Step6 Verification Checklist")
    print("=" * 60)

    test_register_sizes()
    test_b6_recalibration()
    test_pid_plus_micropython()
    test_speed_loop_enable_explicit()
    test_audit_leftovers()

    print("\n--- Holding register mapping ---")
    test_hr_write_mapping()
    test_speed_loop_mapping()
    test_range_validation()
    test_consistency_validation()
    test_coil_write_sync()
    test_coil_reset_pulse()
    test_coil_sync_from_state()

    print("\n--- Input / discrete sync ---")
    test_input_regs_sync()
    test_holding_readback()

    print("\n--- Persistence ---")
    test_persistence_roundtrip()
    test_persistence_corruption()
    test_persistence_debounce()

    print("\n--- Startup ---")
    test_startup_stage_mirror()

    print("\n" + "=" * 60)
    print("Result: %d total, %d passed, %d failed" % (
        _test_count, _pass_count, _fail_count))
    print("=" * 60)

    sys.exit(0 if _fail_count == 0 else 1)

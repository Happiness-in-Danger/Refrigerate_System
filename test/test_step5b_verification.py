#!/usr/bin/env python3
"""
Step5b Verification Checklist

Step5b DoD（依据 PLAN.md §2 / DESIGN.md §6-§7）：
1. 三环独立可投退，输入 quality 非 OK 冻结积分
2. L1 限载可覆盖容量环（禁升速/降容/冷凝风全速）
3. 温控循环 MIN_ON/MIN_OFF 生效
4. 分级阈值单调性校验（warn<unload<trip）
5. 排气温度 L1 降容 + L2 连续 300s 停机
6. L3 重试锁定：1 小时内 3 次 -> retry_lockout，条件消失清
7. Modbus 通讯断走正常停机（不锁存）
8. 蒸发风机无自动调节，RUNNING 期间读 hr13

本脚本只做静态检查和可 mock 的单元测试，不连接硬件。
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
    diff = (a - b) & 0x3FFFFFFF
    if diff > 0x1FFFFFFF:
        diff -= 0x40000000
    return diff

def sleep_ms(ms):
    _time.sleep(ms / 1000.0)

class MockTime:
    ticks_ms = staticmethod(ticks_ms)
    ticks_diff = staticmethod(ticks_diff)
    sleep_ms = staticmethod(sleep_ms)
    sleep = staticmethod(_time.sleep)

sys.modules['time'] = MockTime()

class MockPin:
    def __init__(self, name):
        self.name = name

class MockADC:
    def __init__(self, pin):
        self._value = 32768
    def read(self):
        return self._value

class MockUART:
    def __init__(self, *args, **kwargs):
        pass

class MockSPI:
    def __init__(self, *args, **kwargs):
        pass

sys.modules['pyb'] = type(sys)('pyb')
sys.modules['pyb'].Pin = MockPin
sys.modules['pyb'].ADC = MockADC
sys.modules['pyb'].UART = MockUART
sys.modules['pyb'].SPI = MockSPI
sys.modules['machine'] = type(sys)('machine')
sys.modules['machine'].SoftSPI = MockSPI
sys.modules['adc_direct'] = type(sys)('adc_direct')
sys.modules['uasyncio'] = type(sys)('uasyncio')

import Board.state as state
from Alarm import rules, supervisor
import Compressor.control as comp_ctrl
import Condenser.control as cond_ctrl

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
    state.state_data[state.ST_SENSOR_FAULT] = 0
    state.state_data[state.ST_FLOW_SWITCH] = 1
    state.state_data[state.ST_COMPRESSOR_FAULT] = 0
    state.state_data[state.ST_EVAP_FAN_FAULT] = 0
    state.state_data[state.ST_COND_FAN_FAULT] = 0
    state.state_data[state.ST_MOTOR_SPEED] = 0
    state.fault_reset_request = False
    state.driver_link_ok = True
    state.retry_lockout = False
    state.limit_actions = 0
    state.running_substate = state.RUNNING_THERMO_OFF
    state.comp_throttle_base = 0
    state.cond_fan_throttle_base = 0
    state.last_modbus_frame_ms = _mock_now[0] + 86400000  # 很远未来，避免干扰
    supervisor._rule_timers.clear()
    supervisor._rule_fired.clear()
    supervisor._startup_step = 0
    supervisor._startup_step_start = None
    supervisor._s5_hold_start = None
    supervisor._stopping_start = None
    supervisor._stopping_is_fault = False
    supervisor.driver_link_latched = False
    supervisor._retry_counts.clear()
    supervisor._thermo_on_start = None
    supervisor._thermo_off_start = None
    supervisor._start_times.clear()
    now = ticks_ms()
    for i in range(10):
        state.sensor_quality[i] = state.Q_OK
        state.sensor_checked[i] = 0.0
        state.sensor_checked_ms[i] = now
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 15.0
    state.sensor_checked[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_checked[state.IDX_SUPERHEAT] = 5.0
    state.sensor_checked[state.IDX_CURRENT] = 20.0
    state.sensor_checked[state.IDX_OUTLET_T] = 22.0
    state.sensor_checked[state.IDX_DISCHARGE_T] = 80.0
    for i in range(16, 32):
        state.state_data[i] = 999

def advance_time(ms):
    _mock_now[0] += ms
    now = ticks_ms()
    for i in range(10):
        if state.sensor_quality[i] == state.Q_OK:
            state.sensor_checked_ms[i] = now

# ═══════════════════════════════════════════════════════════
# Static checks
# ═══════════════════════════════════════════════════════════

def check_step5b_params():
    pp = state.protect_params
    check("HP_UNLOAD 存在", "HP_UNLOAD" in pp)
    check("HP_WARN 存在", "HP_WARN" in pp)
    check("I_LIMIT 存在", "I_LIMIT" in pp)
    check("I_UNLOAD 存在", "I_UNLOAD" in pp)
    check("TD_UNLOAD 存在", "TD_UNLOAD" in pp)
    check("TD_TRIP 存在", "TD_TRIP" in pp)
    check("SH_CRIT 存在", "SH_CRIT" in pp)
    check("MIN_ON_S 存在", "MIN_ON_S" in pp)
    check("MIN_OFF_S 存在", "MIN_OFF_S" in pp)
    check("THERMO_HYST 存在", "THERMO_HYST" in pp)
    check("RETRY_MAX 存在", "RETRY_MAX" in pp)
    check("MODBUS_TIMEOUT_S 存在", "MODBUS_TIMEOUT_S" in pp)
    check("COMP_OIL_RETURN_THROTTLE 存在", "COMP_OIL_RETURN_THROTTLE" in pp)
    check("UNLOAD_RATE_THROTTLE_S 存在", "UNLOAD_RATE_THROTTLE_S" in pp)

def check_limit_action_constants():
    check("ACT_INHIBIT_UP 常量", hasattr(state, "ACT_INHIBIT_UP"))
    check("ACT_UNLOAD 常量", hasattr(state, "ACT_UNLOAD"))
    check("ACT_COND_FAN_MAX 常量", hasattr(state, "ACT_COND_FAN_MAX"))
    check("ACT_NORMAL_STOP 常量", hasattr(state, "ACT_NORMAL_STOP"))
    check("limit_actions 变量", hasattr(state, "limit_actions"))

def check_running_substate_constants():
    check("RUNNING_PULLDOWN 常量", hasattr(state, "RUNNING_PULLDOWN"))
    check("RUNNING_MODULATING 常量", hasattr(state, "RUNNING_MODULATING"))
    check("RUNNING_LIMITED 常量", hasattr(state, "RUNNING_LIMITED"))
    check("RUNNING_THERMO_OFF 常量", hasattr(state, "RUNNING_THERMO_OFF"))
    check("running_substate 变量", hasattr(state, "running_substate"))

def check_loop_params():
    check("compressor_control_params 有 throttle_min/max",
          "throttle_min" in state.compressor_control_params and
          "throttle_max" in state.compressor_control_params)
    check("cond_control_params 存在",
          hasattr(state, "cond_control_params"))
    check("cond_control_params 有 setpoint",
          "setpoint" in state.cond_control_params)

def check_l1_rules():
    names = [r[0] for r in rules.RULES]
    check("HIGH_PRESSURE_UNLOAD 规则存在", "HIGH_PRESSURE_UNLOAD" in names)
    check("CURRENT_LIMIT 规则存在", "CURRENT_LIMIT" in names)
    check("CURRENT_UNLOAD 规则存在", "CURRENT_UNLOAD" in names)
    check("LOW_PRESSURE_UNLOAD 规则存在", "LOW_PRESSURE_UNLOAD" in names)
    check("CRITICAL_LOW_SUPERHEAT 规则存在", "CRITICAL_LOW_SUPERHEAT" in names)
    check("DISCHARGE_TEMP_UNLOAD 规则存在", "DISCHARGE_TEMP_UNLOAD" in names)
    check("DISCHARGE_TEMP_TRIP 规则存在", "DISCHARGE_TEMP_TRIP" in names)
    check("MODBUS_COMM_FAULT 规则存在", "MODBUS_COMM_FAULT" in names)

def check_capacity_loop_code():
    content = read_file("Compressor/control.py")
    check("Compressor 实现容量环", "comp_throttle_base" in content)
    check("Compressor 读取 limit_actions", "limit_actions" in content)

def check_cond_loop_code():
    content = read_file("Condenser/control.py")
    check("Condenser 实现冷凝压力环", "cond_fan_throttle_base" in content)
    check("Condenser 读取 limit_actions", "limit_actions" in content)

# ═══════════════════════════════════════════════════════════
# Unit tests
# ═══════════════════════════════════════════════════════════

def test_threshold_monotonicity_ok():
    """阈值单调性正常时允许启动"""
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.error_reg = 0xFFFFFFFF
    supervisor.tick()
    check("阈值正常：SAFE_STOP -> STARTUP",
          state.sys_mode == state.MODE_STARTUP)

def test_threshold_monotonicity_fail():
    """HP_WARN >= HP_UNLOAD 时拒绝启动并置 CONFIG_IO_FAULT"""
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    original = state.protect_params["HP_UNLOAD"]
    state.protect_params["HP_UNLOAD"] = 18.0  # 小于 HP_WARN=19.0
    supervisor.tick()
    ok = (state.sys_mode == state.MODE_SAFE_STOP and
          state.is_fault("CONFIG_IO_FAULT"))
    state.protect_params["HP_UNLOAD"] = original
    state.clear_fault("CONFIG_IO_FAULT")
    check("阈值单调性失败：拒绝启动 + CONFIG_IO_FAULT", ok)

def test_l1_high_pressure_unload():
    """排气压力 > HP_UNLOAD 延时 5s 后产生 ACT_UNLOAD + ACT_COND_FAN_MAX"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 21.0  # > HP_UNLOAD=20.5
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    actions = state.limit_actions
    ok = bool(actions & state.ACT_UNLOAD) and bool(actions & state.ACT_COND_FAN_MAX)
    check("高压限载产生 ACT_UNLOAD + ACT_COND_FAN_MAX", ok,
          "limit_actions=0x%02X" % actions)

def test_l1_current_limit():
    """电流 > I_LIMIT 延时后产生 ACT_INHIBIT_UP"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_CURRENT] = 41.0  # > I_LIMIT=40
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    ok = bool(state.limit_actions & state.ACT_INHIBIT_UP)
    check("电流禁升产生 ACT_INHIBIT_UP", ok,
          "limit_actions=0x%02X" % state.limit_actions)

def test_l1_current_unload():
    """电流 > I_UNLOAD 延时后产生 ACT_UNLOAD"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_CURRENT] = 43.0  # > I_UNLOAD=42
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    ok = bool(state.limit_actions & state.ACT_UNLOAD)
    check("电流降容产生 ACT_UNLOAD", ok,
          "limit_actions=0x%02X" % state.limit_actions)

def test_l1_low_pressure_unload():
    """吸气压力 < LP_UNLOAD 延时后产生 ACT_UNLOAD"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_SUCTION_PSI] = 2.5  # < LP_UNLOAD=2.8
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    ok = bool(state.limit_actions & state.ACT_UNLOAD)
    check("低压限载产生 ACT_UNLOAD", ok,
          "limit_actions=0x%02X" % state.limit_actions)

def test_l1_critical_low_superheat():
    """过热度 < SH_CRIT 延时后产生 ACT_UNLOAD 并关小 EXV"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_SUPERHEAT] = 0.5  # < SH_CRIT=1.0
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    actions = state.limit_actions
    exv_ok = state.supervisor_interlocks["exv_target_pct"] == 0.0
    ok = bool(actions & state.ACT_UNLOAD) and exv_ok
    check("临界低过热产生 ACT_UNLOAD + 关小 EXV", ok,
          "limit_actions=0x%02X, exv=%s" % (actions, state.supervisor_interlocks["exv_target_pct"]))

def test_l1_discharge_temp_unload():
    """排气温度 > TD_UNLOAD 延时后产生 ACT_UNLOAD"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_T] = 106.0  # > TD_UNLOAD=105
    supervisor.tick()
    advance_time(5500)
    supervisor.tick()
    ok = bool(state.limit_actions & state.ACT_UNLOAD)
    check("排气温度降容产生 ACT_UNLOAD", ok,
          "limit_actions=0x%02X" % state.limit_actions)

def test_discharge_temp_trip_continuous():
    """排气温度 > TD_TRIP 必须连续 300s；中途降温重新计时"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_T] = 116.0

    supervisor.tick()
    advance_time(200000)  # 200s
    supervisor.tick()
    ok1 = not state.is_fault("HIGH_DISCHARGE_TEMP")

    # 中途降温
    state.sensor_checked[state.IDX_DISCHARGE_T] = 100.0
    supervisor.tick()
    advance_time(10000)
    state.sensor_checked[state.IDX_DISCHARGE_T] = 116.0
    supervisor.tick()
    advance_time(200000)  # 再 200s，仍未到 300s
    supervisor.tick()
    ok2 = not state.is_fault("HIGH_DISCHARGE_TEMP")

    advance_time(110000)  # 累计连续 300s+
    supervisor.tick()
    ok3 = state.is_fault("HIGH_DISCHARGE_TEMP")

    check("排温停机需连续 300s", ok1 and ok2 and ok3)

def test_modbus_comm_normal_stop():
    """Modbus 断 60s 后进入 STOPPING，不锁存 error_reg"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    # 已 70s 无帧，条件成立并启动计时
    state.last_modbus_frame_ms = ticks_ms() - 70000
    supervisor.tick()
    advance_time(61000)  # 再等 61s，超过 60s 延时
    supervisor.tick()
    ok = (state.sys_mode == state.MODE_STOPPING and
          state.error_reg == 0xFFFFFFFF)
    check("Modbus 通讯断 -> 正常停机（不锁存）", ok,
          "mode=%d error_reg=0x%08X" % (state.sys_mode, state.error_reg))

def test_thermo_cycle_r1_to_r4():
    """RUNNING 中达到设定且 MIN_ON 满足 -> R4 THERMO_OFF"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_PULLDOWN
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_OUTLET_T] = 21.0  # <= setpoint=22
    state.state_data[state.ST_MOTOR_SPEED] = 1500    # <= rpm_min=1500
    supervisor._thermo_on_start = ticks_ms() - 200000  # MIN_ON=180s 已满足
    supervisor.tick()
    check("温控循环 R1/R2 -> R4", state.running_substate == state.RUNNING_THERMO_OFF)

def test_thermo_cycle_r4_to_r1():
    """R4 中回温超回差且 MIN_OFF 满足 -> R1"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_THERMO_OFF
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_OUTLET_T] = 25.0  # > setpoint+hyst=24
    supervisor._thermo_off_start = ticks_ms() - 310000  # MIN_OFF=300s 已满足
    supervisor.tick()
    check("温控循环 R4 -> R1", state.running_substate == state.RUNNING_PULLDOWN)

def _trigger_high_pressure_latch():
    """辅助：在 RUNNING 中触发高压并等待锁存"""
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0  # > HP_MAX
    supervisor.tick()
    advance_time(4000)
    supervisor.tick()


def _reset_and_restart():
    """辅助：停机、手动复位、重新启动并进入 RUNNING"""
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 15.0
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    supervisor.tick()  # -> STOPPING
    # 故障停机需等 FAULT_FAN_HOLD_MS 后才能到 SAFE_STOP
    advance_time(state.protect_params["FAULT_FAN_HOLD_MS"] + 1000)
    supervisor.tick()  # -> SAFE_STOP
    state.fault_reset_request = True
    supervisor.tick()  # 复位
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    supervisor.tick()  # SAFE_STOP -> STARTUP
    state.sys_mode = state.MODE_RUNNING
    advance_time(1000)


def test_retry_lockout():
    """同一故障 1 小时内 3 次锁存 -> retry_lockout"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    for i in range(3):
        _trigger_high_pressure_latch()
        ok_latched = state.is_fault("HIGH_PRESSURE_FAULT")
        if i < 2:
            _reset_and_restart()
    check("L3 重试锁定：3 次锁存 -> retry_lockout", state.retry_lockout)


def test_capacity_loop_direction():
    """温度偏高时容量环输出应增加，温度偏低时应减小（CRITICAL-1 闭环验证）"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_PULLDOWN
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    idx = state.IDX_OUTLET_T

    # 温度偏高：应增加油门
    state.comp_throttle_base = 500
    state.sensor_checked[idx] = 30.0  # > setpoint=22.0，error=8
    state.sensor_quality[idx] = state.Q_OK
    state.sensor_checked_ms[idx] = ticks_ms()
    comp_ctrl._reset_cap_pid()
    comp_ctrl._last_cap_ms = 0
    advance_time(10000)
    comp_ctrl._update_capacity_loop(ticks_ms())
    base_high_first = state.comp_throttle_base
    advance_time(10000)
    comp_ctrl._update_capacity_loop(ticks_ms())
    base_high_second = state.comp_throttle_base
    ok_high = base_high_second > base_high_first
    check("温度偏高 -> 容量环油门增加", ok_high,
          "base=%d -> %d" % (base_high_first, base_high_second))

    # 温度偏低：应减小油门
    state.comp_throttle_base = 500
    state.sensor_checked[idx] = 15.0  # < setpoint=22.0，error=-7
    state.sensor_quality[idx] = state.Q_OK
    state.sensor_checked_ms[idx] = ticks_ms()
    comp_ctrl._reset_cap_pid()
    comp_ctrl._last_cap_ms = 0
    advance_time(10000)
    comp_ctrl._update_capacity_loop(ticks_ms())
    base_low_first = state.comp_throttle_base
    advance_time(10000)
    comp_ctrl._update_capacity_loop(ticks_ms())
    base_low_second = state.comp_throttle_base
    ok_low = base_low_second < base_low_first
    check("温度偏低 -> 容量环油门减小", ok_low,
          "base=%d -> %d" % (base_low_first, base_low_second))


def test_condenser_pressure_loop_direction():
    """排气压力偏高时，冷凝压力环输出应增加"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_PULLDOWN
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    idx = state.IDX_DISCHARGE_PSI
    state.cond_fan_throttle_base = 500
    state.sensor_checked[idx] = 20.0  # > setpoint=12.0，error=8
    state.sensor_quality[idx] = state.Q_OK
    state.sensor_checked_ms[idx] = ticks_ms()
    cond_ctrl._reset_pres_pid()
    cond_ctrl._last_pres_ms = 0
    advance_time(2000)
    cond_ctrl._update_pressure_loop(ticks_ms())
    base_first = state.cond_fan_throttle_base
    advance_time(2000)
    cond_ctrl._update_pressure_loop(ticks_ms())
    base_second = state.cond_fan_throttle_base
    ok = base_second > base_first
    check("排气压力偏高 -> 冷凝风机油门增加", ok,
          "base=%d -> %d" % (base_first, base_second))


def test_thermo_cycle_quality_gate():
    """出液温度传感器 BAD 时不应误进入 R4 THERMO_OFF（MAJOR-1 质量门禁）"""
    reset_state()
    state.sys_mode = state.MODE_RUNNING
    state.running_substate = state.RUNNING_PULLDOWN
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    idx = state.IDX_OUTLET_T
    state.sensor_checked[idx] = 21.0  # <= setpoint=22.0
    state.sensor_quality[idx] = state.Q_BAD
    state.sensor_checked_ms[idx] = ticks_ms()
    state.state_data[state.ST_MOTOR_SPEED] = 1500
    state.state_data[state.ST_MOTOR_SPEED_MIN] = 1500
    supervisor._thermo_on_start = ticks_ms() - 200000  # MIN_ON 已满足
    supervisor.tick()
    check("温控循环：传感器 BAD 不误进入 R4",
          state.running_substate != state.RUNNING_THERMO_OFF,
          "substate=%d" % state.running_substate)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Step5b Verification Checklist")
    print("=" * 60)

    check_step5b_params()
    check_limit_action_constants()
    check_running_substate_constants()
    check_loop_params()
    check_l1_rules()
    check_capacity_loop_code()
    check_cond_loop_code()

    print("\n--- Unit tests ---")
    test_threshold_monotonicity_ok()
    test_threshold_monotonicity_fail()
    test_l1_high_pressure_unload()
    test_l1_current_limit()
    test_l1_current_unload()
    test_l1_low_pressure_unload()
    test_l1_critical_low_superheat()
    test_l1_discharge_temp_unload()
    test_discharge_temp_trip_continuous()
    test_modbus_comm_normal_stop()
    test_thermo_cycle_r1_to_r4()
    test_thermo_cycle_r4_to_r1()
    test_retry_lockout()
    test_capacity_loop_direction()
    test_condenser_pressure_loop_direction()
    test_thermo_cycle_quality_gate()

    print("\n" + "=" * 60)
    print("Result: %d total, %d passed, %d failed" % (
        _test_count, _pass_count, _fail_count))
    print("=" * 60)

    sys.exit(0 if _fail_count == 0 else 1)

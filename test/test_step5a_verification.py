#!/usr/bin/env python3
"""
Step5a Verification Checklist

Step5a DoD（依据 PLAN.md §2 / DESIGN.md §5-§8）：
1. 三设备模块消费 supervisor_interlocks 全键（解 P0-2）
2. ESC 遥测写入 state_data[16-31]，无效通道=999
3. error_reg 32-bit + delay_slots[8]
4. 压缩机/风机故障规则生效
5. 流量保护规则生效（FLOW_ENABLE=0 可禁用）
6. 手动模式 hr9=1 时压缩机/冷凝风机/EXV 受 hr11-14 控制
7. 蒸发风机 RUNNING 期间始终读 hr13
8. S1-S6 启停时序修订可真实走通

本脚本只做静态检查和可 mock 的单元测试，不连接硬件。
"""

import sys
import os
import time as _time
import inspect

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

# ═══════════════════════════════════════════════════════════
# Static checks
# ═══════════════════════════════════════════════════════════

def check_interlocks_consumed():
    """P0-2: supervisor_interlocks 全键必须有消费者"""
    expected_keys = [
        "comp_enable", "comp_throttle_cmd",
        "evap_fan_enable", "evap_fan_throttle_cmd",
        "cond_fan_enable", "cond_fan_throttle_cmd",
        "exv_enable", "exv_target_pct",
    ]
    consumers = {
        "Compressor/control.py": ["comp_enable", "comp_throttle_cmd"],
        "Evaporator/control.py": ["evap_fan_enable", "evap_fan_throttle_cmd"],
        "Condenser/control.py": ["cond_fan_enable", "cond_fan_throttle_cmd"],
        "EXV/Valve_CRTL.py": ["exv_enable", "exv_target_pct"],
    }
    all_ok = True
    for fpath, keys in consumers.items():
        content = read_file(fpath)
        for k in keys:
            if ('["%s"]' % k) not in content and ("['%s']" % k) not in content:
                check("interlocks 消费者: %s 读 %s" % (fpath, k), False)
                all_ok = False
    check("interlocks 全键有消费者", all_ok)

def check_esc_telemetry_indices():
    """ESC 遥测索引 16-31 已定义"""
    required = [
        "ST_ESC0_RPM", "ST_ESC0_TEMP", "ST_ESC0_VOLTAGE", "ST_ESC0_CURRENT",
        "ST_ESC1_RPM", "ST_ESC1_TEMP", "ST_ESC1_VOLTAGE", "ST_ESC1_CURRENT",
        "ST_ESC2_RPM", "ST_ESC2_TEMP", "ST_ESC2_VOLTAGE", "ST_ESC2_CURRENT",
        "ST_ESC3_RPM", "ST_ESC3_TEMP", "ST_ESC3_VOLTAGE", "ST_ESC3_CURRENT",
    ]
    content = read_file("Board/state.py")
    missing = [n for n in required if n not in content]
    check("ESC 遥测索引定义完整", len(missing) == 0, str(missing))

def check_state_data_length():
    check("state_data 长度 >= 33 (含 ST_FLOW_SWITCH=32)",
          len(state.state_data) >= 33,
          "实际长度=%d" % len(state.state_data))

def check_error_reg_32bit():
    check("error_reg 为 32-bit",
          state.error_reg == 0xFFFFFFFF,
          "初值=0x%08X" % state.error_reg)
    # 测试 set_fault 位 15 (FLOW_FAULT，2026-09-19 重编号后)
    bit = state.ERROR_BITS["FLOW_FAULT"]
    state.set_fault("FLOW_FAULT")
    bit_clear = not (state.error_reg & (1 << bit))
    state.clear_fault("FLOW_FAULT")
    bit_set = bool(state.error_reg & (1 << bit))
    check("set_fault/clear_fault 兼容 FLOW_FAULT 位 %d" % bit, bit_clear and bit_set)

def check_delay_slots():
    slots = state.protect_params.get("delay_slots", [])
    check("delay_slots 有 8 个槽", len(slots) == 8, str(slots))
    check("delay_slots 值与文档一致",
          slots == [0, 5, 10, 30, 45, 60, 300, 900],
          str(slots))

def check_flow_fault_rule():
    names = [r[0] for r in rules.RULES]
    check("FLOW_FAULT 规则存在", "FLOW_FAULT" in names)
    check("COMPRESSOR_FAULT 规则存在", "COMPRESSOR_FAULT" in names)
    check("EVAP_FAN_FAULT 规则存在", "EVAP_FAN_FAULT" in names)
    check("COND_FAN_FAULT 规则存在", "COND_FAN_FAULT" in names)

def check_manual_params():
    mp = state.manual_params
    check("manual_params 含 MANUAL_MODE_ENABLE", "MANUAL_MODE_ENABLE" in mp)
    check("manual_params 含 MANUAL_EXV_PCT", "MANUAL_EXV_PCT" in mp)
    # 依据当前代码口径（油门语义）
    check("manual_params 含 MANUAL_COMP_THROTTLE", "MANUAL_COMP_THROTTLE" in mp)
    check("manual_params 含 MANUAL_COND_FAN_THROTTLE", "MANUAL_COND_FAN_THROTTLE" in mp)
    check("evap_fan_user_params 含 EVAP_FAN_USER_THROTTLE",
          "EVAP_FAN_USER_THROTTLE" in state.evap_fan_user_params)

def check_driver_telemetry_bug():
    """Driver.py 是否存在 ESC_teLEMETRY_CFG 大小写笔误"""
    content = read_file("HAL/Driver.py")
    has_typo = "ESC_teLEMETRY_CFG" in content
    check("Driver.py 无 ESC_TELEMETRY_CFG 大小写笔误", not has_typo,
          "发现 ESC_teLEMETRY_CFG（应为 ESC_TELEMETRY_CFG）")

def check_driver_link_ok_duplicated():
    content = read_file("Board/state.py")
    cnt = content.count("driver_link_ok = True")
    check("state.py driver_link_ok 只定义一次", cnt == 1,
          "发现 %d 处 driver_link_ok = True" % cnt)

# ═══════════════════════════════════════════════════════════
# Unit tests (with mocks)
# ═══════════════════════════════════════════════════════════

def reset_state():
    state.sys_mode = state.MODE_SAFE_STOP
    state.error_reg = 0xFFFFFFFF
    state.sensor_fault_reg = 0xFFFF
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    state.state_data[state.ST_SENSOR_FAULT] = 0
    state.state_data[state.ST_FLOW_SWITCH] = 1
    state.fault_reset_request = False
    state.driver_link_ok = True
    supervisor._rule_timers.clear()
    supervisor._rule_fired.clear()
    supervisor._startup_step = 0
    supervisor._startup_step_start = None
    supervisor._s5_hold_start = None
    supervisor._stopping_start = None
    supervisor._stopping_is_fault = False
    supervisor.driver_link_latched = False
    now = ticks_ms()
    for i in range(10):
        state.sensor_quality[i] = state.Q_OK
        state.sensor_checked[i] = 0.0
        state.sensor_checked_ms[i] = now
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 15.0
    state.sensor_checked[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_checked[state.IDX_SUPERHEAT] = 5.0
    state.sensor_checked[state.IDX_CURRENT] = 20.0
    for i in range(16, 32):
        state.state_data[i] = 999

def advance_time(ms):
    _mock_now[0] += ms
    now = ticks_ms()
    for i in range(10):
        if state.sensor_quality[i] == state.Q_OK:
            state.sensor_checked_ms[i] = now

def test_flow_fault_triggers():
    """FLOW_ENABLE=1 且 DI=0 时，FLOW_FAULT 规则延时到点锁存"""
    reset_state()
    state.protect_params["FLOW_ENABLE"] = 1
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.state_data[state.ST_FLOW_SWITCH] = 0
    state.sys_mode = state.MODE_RUNNING

    supervisor.tick()
    ok = "FLOW_FAULT" in supervisor._rule_timers
    advance_time(10500)  # 槽 2 = 10s
    supervisor.tick()
    ok = ok and state.is_fault("FLOW_FAULT")
    check("流量保护：FLOW_ENABLE=1 + DI=0 → 锁存 FLOW_FAULT", ok)

def test_flow_fault_disabled():
    """FLOW_ENABLE=0 时，即使 DI=0 也不计时"""
    reset_state()
    state.protect_params["FLOW_ENABLE"] = 0
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.state_data[state.ST_FLOW_SWITCH] = 0
    state.sys_mode = state.MODE_RUNNING

    supervisor.tick()
    check("流量保护：FLOW_ENABLE=0 时禁用",
          "FLOW_FAULT" not in supervisor._rule_timers)

def test_compressor_fault_rule():
    """Compressor 上报 ST_COMPRESSOR_FAULT 事件位 → COMPRESSOR_FAULT 规则计时"""
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sys_mode = state.MODE_RUNNING
    # 模拟 Compressor 模块检测到 ESC0 rpm 异常后写入事件位
    state.state_data[state.ST_COMPRESSOR_FAULT] = 1

    supervisor.tick()
    check("压缩机故障规则启动计时",
          "COMPRESSOR_FAULT" in supervisor._rule_timers)

def test_fan_fault_rules():
    """风机故障事件位 → EVAP/COND_FAN_FAULT 计时（事件位由设备模块设置）"""
    reset_state()
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sys_mode = state.MODE_RUNNING
    # 直接模拟设备模块上报事件位
    state.state_data[state.ST_EVAP_FAN_FAULT] = 1
    state.state_data[state.ST_COND_FAN_FAULT] = 1

    supervisor.tick()
    check("蒸发风机故障规则启动计时",
          "EVAP_FAN_FAULT" in supervisor._rule_timers)
    check("冷凝风机故障规则启动计时",
          "COND_FAN_FAULT" in supervisor._rule_timers)

def test_manual_mode_evap_fan_hr13():
    """RUNNING 期间蒸发风机读取 EVAP_FAN_USER_THROTTLE（hr13）"""
    # 静态检查 Evaporator 控制模块源码
    content = read_file("Evaporator/control.py")
    cond = ('MODE_RUNNING' in content and
            'EVAP_FAN_USER_THROTTLE' in content and
            'evap_fan_user_params' in content)
    check("蒸发风机 RUNNING 期间读 hr13 用户油门", cond)

def test_device_modules_no_rpm_loop():
    """
    检查设备模块是否实现转速环（依据 DESIGN.md v3.2 §4.2/§7）。
    转速环需要：读取 ST_ESCx_RPM 反馈 + 使用 PID/增量控制器 + 根据误差调节 throttle。
    当前若直接把 *_throttle_cmd 发给 ESC，则判定为未实现转速环。
    """
    for fpath in ["Compressor/control.py", "Condenser/control.py", "Evaporator/control.py"]:
        content = read_file(fpath)
        has_pid = "IncrementalController" in content or "pid" in content.lower()
        reads_feedback = "ST_ESC" in content
        writes_throttle = "set_throttle" in content
        # 转速环判定：必须有 PID 实例且读取反馈
        has_rpm_loop = has_pid and reads_feedback
        check("%s 实现转速环" % fpath, has_rpm_loop,
              "直接下发 throttle，未见 PID 转速闭环" if writes_throttle else "")

# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Step5a Verification Checklist")
    print("=" * 60)

    check_interlocks_consumed()
    check_esc_telemetry_indices()
    check_state_data_length()
    check_error_reg_32bit()
    check_delay_slots()
    check_flow_fault_rule()
    check_manual_params()
    check_driver_telemetry_bug()
    check_driver_link_ok_duplicated()

    print("\n--- Unit tests ---")
    test_flow_fault_triggers()
    test_flow_fault_disabled()
    test_compressor_fault_rule()
    test_fan_fault_rules()
    test_manual_mode_evap_fan_hr13()
    test_device_modules_no_rpm_loop()

    print("\n" + "=" * 60)
    print("Result: %d total, %d passed, %d failed" % (
        _test_count, _pass_count, _fail_count))
    print("=" * 60)

    sys.exit(0 if _fail_count == 0 else 1)

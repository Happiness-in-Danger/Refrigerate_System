#!/usr/bin/env python3
"""
Step3 Verification Test Suite

测试 Step3 DoD（ARCHITECTURE_PLAN.md §11 步3）：
- 报警延时表（§9）
- Supervisor 状态机（§8.1/§8.3）
- 手动复位协议（§8.3.6）

依赖：CPython 模拟 MicroPython time/pyb/uasyncio
"""

import sys
import os
import time as _time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ═══════════════════════════════════════════════════════════
# Mock MicroPython modules
# ═══════════════════════════════════════════════════════════
_start_ticks = int(_time.time() * 1000)
_mock_now = [_start_ticks]  # 可变容器，允许测试注入时间

def ticks_ms():
    return _mock_now[0]

def ticks_diff(a, b):
    # MicroPython 30-bit wrap-safe
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
    time = staticmethod(_time.time)  # For test 14

sys.modules['time'] = MockTime()

# Mock pyb/machine/uasyncio
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

# Now import actual modules
import Board.state as state
from Alarm import rules, supervisor

# ═══════════════════════════════════════════════════════════
# Test utilities
# ═══════════════════════════════════════════════════════════
_test_count = 0
_pass_count = 0
_fail_count = 0

def advance_time(ms):
    """推进模拟时间（ms），同时刷新传感器时间戳防止心跳误触发"""
    _mock_now[0] += ms
    # 刷新传感器时间戳，模拟 Sensor 协程持续更新
    now = ticks_ms()
    for i in range(10):
        if state.sensor_quality[i] == state.Q_OK:
            state.sensor_checked_ms[i] = now

def reset_state():
    """重置 state + supervisor 到初始状态"""
    state.sys_mode = state.MODE_BOOT
    state.error_reg = 0xFFFFFFFF
    state.sensor_fault_reg = 0xFFFF
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    state.state_data[state.ST_SENSOR_FAULT] = 0
    state.fault_reset_request = False
    state.driver_link_ok = True
    
    # 清空 supervisor 模块状态
    supervisor._rule_timers.clear()
    supervisor._rule_fired.clear()
    supervisor._startup_step = 0
    supervisor._startup_step_start = None
    supervisor._s5_hold_start = None
    supervisor._stopping_start = None
    supervisor._stopping_is_fault = False
    supervisor.driver_link_latched = False
    # Step5b 新增状态（防止回归测试间状态污染）
    supervisor._retry_counts.clear()
    supervisor._thermo_on_start = None
    supervisor._thermo_off_start = None
    supervisor._start_times.clear()
    state.retry_lockout = False
    state.limit_actions = 0
    state.running_substate = state.RUNNING_PULLDOWN
    state.comp_throttle_base = 0
    state.cond_fan_throttle_base = 0

    # 传感器初始化为 Q_OK，并设置最近更新时间为当前时刻
    now = ticks_ms()
    for i in range(10):
        state.sensor_quality[i] = state.Q_OK
        state.sensor_checked[i] = 0.0
        state.sensor_checked_ms[i] = now  # 防止心跳误触发
    
    # 设置合理初值
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 15.0  # bara
    state.sensor_checked[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_checked[state.IDX_SUPERHEAT] = 5.0
    state.sensor_checked[state.IDX_CURRENT] = 20.0

    # ESC 遥测初始化为无效，避免启动时序残留反馈
    for i in range(16, 32):
        state.state_data[i] = 999

    # 清空 supervisor_interlocks，避免上一测试残留使能
    il = state.supervisor_interlocks
    il["comp_enable"] = False
    il["comp_throttle_cmd"] = 0
    il["evap_fan_enable"] = False
    il["evap_fan_throttle_cmd"] = 0
    il["cond_fan_enable"] = False
    il["cond_fan_throttle_cmd"] = 0
    il["exv_enable"] = False
    il["exv_target_pct"] = 0.0

    _mock_now[0] = _start_ticks

def test(name, func):
    """运行单个测试"""
    global _test_count, _pass_count, _fail_count
    _test_count += 1
    print("\n[%02d] %s" % (_test_count, name))
    print("-" * 60)
    reset_state()
    try:
        func()
        print("✓ PASS")
        _pass_count += 1
    except AssertionError as e:
        print("✗ FAIL: %s" % e)
        _fail_count += 1
    except Exception as e:
        print("✗ ERROR: %s" % e)
        _fail_count += 1

# ═══════════════════════════════════════════════════════════
# Test cases
# ═══════════════════════════════════════════════════════════

def test_01_normal_state():
    """1. 正常状态：sys_mode 初始化正确，无报警"""
    assert state.sys_mode == state.MODE_BOOT
    assert state.error_reg == 0xFFFFFFFF, "error_reg 初值应为全正常"
    supervisor.tick()
    assert state.sys_mode == state.MODE_SELF_TEST
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP


def test_02_single_alarm_trigger():
    """2. 单个报警产生：高压超限触发计时"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0  # > HP_MAX(22.0)
    
    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" in supervisor._rule_timers
    assert not state.is_fault("HIGH_PRESSURE_FAULT"), "未到延时不应锁存"


def test_03_alarm_before_delay():
    """3. 报警未达到延时时间：持续触发但未锁存"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    
    supervisor.tick()
    start = supervisor._rule_timers.get("HIGH_PRESSURE_FAULT")
    assert start is not None
    
    advance_time(2000)  # 2s < HP_DELAY_S(3s)
    supervisor.tick()
    assert not state.is_fault("HIGH_PRESSURE_FAULT")
    assert state.sys_mode == state.MODE_RUNNING


def test_04_alarm_delay_reached():
    """4. 报警达到延时时间：锁存 error_reg 并执行动作"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    
    supervisor.tick()
    advance_time(3100)  # > HP_DELAY_S(3s)
    supervisor.tick()
    
    assert state.is_fault("HIGH_PRESSURE_FAULT"), "应锁存高压故障"
    assert state.sys_mode == state.MODE_STOPPING, "应进入停机序列"


def test_05_alarm_recovery():
    """5. 报警恢复：条件消失前未锁存的规则计时器清零"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    
    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" in supervisor._rule_timers
    
    advance_time(2000)  # 2s 未到点
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 20.0  # 恢复
    supervisor.tick()
    
    assert "HIGH_PRESSURE_FAULT" not in supervisor._rule_timers
    assert not state.is_fault("HIGH_PRESSURE_FAULT")


def test_06_alarm_timer_clear():
    """6. 报警计时器正确清零：条件抖动不累积"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    
    # 第一次触发
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    supervisor.tick()
    advance_time(1000)
    
    # 恢复
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 20.0
    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" not in supervisor._rule_timers
    
    # 第二次触发，从零开始
    advance_time(1000)
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    supervisor.tick()
    t0 = supervisor._rule_timers["HIGH_PRESSURE_FAULT"]
    
    advance_time(2500)  # 距第二次触发 2.5s
    supervisor.tick()
    assert not state.is_fault("HIGH_PRESSURE_FAULT"), "2.5s < 3s，不应锁存"


def test_07_multiple_alarms():
    """7. 多个报警同时出现：独立计时，全部锁存"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0  # 高压
    state.sensor_checked[state.IDX_CURRENT] = 50.0         # 过流
    
    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" in supervisor._rule_timers
    assert "OVERCURRENT_FAULT" in supervisor._rule_timers
    
    advance_time(3100)  # HP 3s, OC 3s
    supervisor.tick()
    
    assert state.is_fault("HIGH_PRESSURE_FAULT")
    assert state.is_fault("OVERCURRENT_FAULT")
    assert state.sys_mode == state.MODE_STOPPING


def test_08_safe_stop_trigger():
    """8. SAFE_STOP 触发：高压到点后停机，状态机进 STOPPING → SAFE_STOP"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    
    supervisor.tick()
    advance_time(3100)
    supervisor.tick()
    assert state.sys_mode == state.MODE_STOPPING
    
    # 推进停机序列（FAULT_FAN_HOLD_MS = 15000）
    advance_time(16000)
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP


def test_09_safe_stop_no_auto_restart():
    """9. SAFE_STOP 后恢复输入但未手动复位：禁止自动启动"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    
    supervisor.tick()
    advance_time(3100)
    supervisor.tick()
    assert state.is_fault("HIGH_PRESSURE_FAULT")
    
    # 推进到 SAFE_STOP
    advance_time(16000)
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP
    
    # 传感器恢复
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 20.0
    supervisor.tick()
    
    # ENABLE 仍为 1，但因锁存故障未清除 → 禁止进 STARTUP
    assert state.sys_mode == state.MODE_SAFE_STOP
    assert state.is_fault("HIGH_PRESSURE_FAULT")


def test_10_manual_reset():
    """10. 手动复位：清除锁存位，条件消失后允许启动"""
    state.sys_mode = state.MODE_SAFE_STOP
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.set_fault("HIGH_PRESSURE_FAULT")
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 20.0  # 已恢复
    
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP  # 锁存故障 → 禁止启动
    
    # 手动复位
    state.fault_reset_request = True
    supervisor.tick()
    assert not state.is_fault("HIGH_PRESSURE_FAULT")
    
    # 下一周期：条件已消失 → 保持清除 → 允许进 STARTUP
    supervisor.tick()
    assert state.sys_mode == state.MODE_STARTUP


def test_11_reset_condition_not_met():
    """11. 手动复位条件不满足时拒绝：RUNNING 期间拒绝复位"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1  # 保持使能
    state.fault_reset_request = True
    supervisor.tick()
    # 复位在 tick 内消费但不受理（sys_mode 非 SAFE_STOP）
    # ENABLE=1 时 RUNNING 不应自动停机
    assert state.sys_mode == state.MODE_RUNNING


def test_12_alarm_flapping():
    """12. 重复报警/报警抖动：计时器正确重置，不误触发"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    
    for _ in range(3):
        state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
        supervisor.tick()
        advance_time(2000)  # 2s 未到点
        
        state.sensor_checked[state.IDX_DISCHARGE_PSI] = 20.0
        supervisor.tick()
        assert not state.is_fault("HIGH_PRESSURE_FAULT")
        advance_time(1000)
    
    # 最后一次持续触发
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    supervisor.tick()
    advance_time(3100)
    supervisor.tick()
    assert state.is_fault("HIGH_PRESSURE_FAULT")


def test_13_ticks_wraparound():
    """13. ticks_ms 回绕时的计时行为：ticks_diff 回绕安全"""
    # 模拟接近 30-bit 边界
    wrap_base = 0x3FFFFFF0
    _mock_now[0] = wrap_base
    # 刷新传感器时间戳，避免 age 计算因跨边界而异常
    now = ticks_ms()
    for i in range(10):
        state.sensor_checked_ms[i] = now

    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0

    supervisor.tick()
    t0 = supervisor._rule_timers["HIGH_PRESSURE_FAULT"]

    # 跨越回绕（30-bit: 0x3FFFFFFF → 0x00000000）
    _mock_now[0] = (t0 + 3100) & 0x3FFFFFFF
    # 跨越边界后再次刷新时间戳，保持数据新鲜
    now = ticks_ms()
    for i in range(10):
        state.sensor_checked_ms[i] = now
    supervisor.tick()

    assert state.is_fault("HIGH_PRESSURE_FAULT"), "回绕后延时计算仍正确"


def test_14_supervisor_nonblocking():
    """14. Supervisor 不阻塞 uasyncio 主循环：tick() 同步快速返回"""
    import time as _realtime
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    
    t0 = _realtime.time()
    for _ in range(100):
        supervisor.tick()
    elapsed = _realtime.time() - t0
    
    assert elapsed < 0.1, "100 次 tick 应在 100ms 内完成（实测 %.2fms）" % (elapsed * 1000)


def test_15_step3_no_break_step2():
    """15. Step3 不破坏 Step2 已验收的 quality/fault 行为"""
    # Step2 验收点：sensor_fault_reg 更新、ST_SENSOR_FAULT 写入
    state.sensor_data[state.IDX_SUCTION_T] = 999
    state.sensor_data[state.IDX_DISCHARGE_T] = 50.0
    state.update_all_sensor_faults()
    
    assert not (state.sensor_fault_reg & (1 << 0)), "哨兵值应置位 SUCTION_T_FAULT"
    assert (state.sensor_fault_reg & (1 << 1)), "正常值应清除 DISCHARGE_T_FAULT"
    
    # supervisor tick 更新 ST_SENSOR_FAULT
    supervisor.tick()
    assert state.state_data[state.ST_SENSOR_FAULT] == 1, "存在故障应置 ST_SENSOR_FAULT"


def _feed_startup_feedback():
    """
    模拟执行机构反馈（每 tick 前调用）
    新时序（2026-09-03）：S1 冷凝风机 → S2 蒸发风机 → S3 EXV → S4 压缩机
    2026-09-03 修正：改为喂 ESC 遥测反馈，不再喂旧的 ST_*_RPM 字段
    """
    pp = state.protect_params
    step = supervisor._startup_step
    if step >= 1:
        # S1: 冷凝风机 → ESC2 遥测
        state.state_data[state.ST_ESC2_RPM] = 1500  # 模拟反馈转速
    if step >= 2:
        # S2: 蒸发风机 → ESC1 遥测
        state.state_data[state.ST_ESC1_RPM] = 1200
    if step >= 3:
        # S3: EXV 开度
        state.state_data[state.ST_VALVE_POS] = pp["EXV_START_POS"]
    if step >= 4:
        # S4: 压缩机 → ESC0 遥测；同时镜像到 ST_MOTOR_SPEED（Compressor 模块行为）
        state.state_data[state.ST_ESC0_RPM] = 3000
        state.state_data[state.ST_MOTOR_SPEED] = 3000


def test_16_startup_sequence_full():
    """扩展：启动时序 S0-S6 全流程（模拟执行机构反馈）"""
    state.sys_mode = state.MODE_SAFE_STOP
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    # 清零反馈，S1 起逐步喂入（2026-09-03 修正：初始化 ESC 遥测为无效）
    state.state_data[state.ST_VALVE_POS] = 0
    state.state_data[state.ST_ESC0_RPM] = 999  # 压缩机遥测无效
    state.state_data[state.ST_ESC1_RPM] = 999  # 蒸发风机遥测无效
    state.state_data[state.ST_ESC2_RPM] = 999  # 冷凝风机遥测无效

    supervisor.tick()
    assert state.sys_mode == state.MODE_STARTUP, "SAFE_STOP -> STARTUP"

    # 先风后机顺序记录
    order = []
    for _ in range(400):          # 最多 40s 模拟时间
        _feed_startup_feedback()
        supervisor.tick()
        st = supervisor._startup_step
        if not order or order[-1] != st:
            order.append(st)
        if state.sys_mode == state.MODE_RUNNING:
            break
        advance_time(100)

    assert state.sys_mode == state.MODE_RUNNING, \
        "应进入 RUNNING（实际 sys_mode=%d step=%d）" % (
            state.sys_mode, supervisor._startup_step)
    # 校验 S1->S2->S3->S4->S5 单调推进（先风后机）
    mono = [s for s in order if s != 0]
    assert mono == sorted(mono), "启动步必须单调推进，实际 %s" % mono


def test_16b_startup_timeout_failsafe():
    """扩展：启动步无反馈超时 → 锁存故障位 + SAFE_STOP（禁止自动重启）

    新时序（2026-09-03）：S1 = 冷凝风机，判定读 ST_ESC2_RPM
    """
    state.sys_mode = state.MODE_SAFE_STOP
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.state_data[state.ST_ESC2_RPM] = 999  # S1 冷凝风机无有效反馈
    state.state_data[state.ST_COND_FAN_RPM] = 0

    supervisor.tick()
    assert state.sys_mode == state.MODE_STARTUP

    # S1 不喂冷凝风机反馈 → S1_TIMEOUT_MS(10s) 后失败
    for _ in range(120):
        advance_time(100)
        supervisor.tick()
        if state.sys_mode == state.MODE_SAFE_STOP:
            break

    assert state.sys_mode == state.MODE_SAFE_STOP, "S1 超时应进 SAFE_STOP"
    assert state.is_fault("COND_FAN_FAULT"), "S1 超时应锁存 COND_FAN_FAULT"

    # ENABLE 仍为 1，但锁存故障未复位 → 不得自动重启
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP, "锁存故障未复位不得自动重启"


def test_17_low_superheat_no_stop():
    """扩展：低过热度报警不停机，仅关小 EXV"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_SUPERHEAT] = 1.0  # < SH_MIN(2.0)
    
    supervisor.tick()
    assert "LOW_SUPERHEAT_FAULT" in supervisor._rule_timers
    
    advance_time(5100)  # LSH_DELAY_S = 5s
    supervisor.tick()
    
    assert state.is_fault("LOW_SUPERHEAT_FAULT")
    # 低 SH 为非锁存故障，触发 ALARM 状态（可恢复故障，继续运行）
    assert state.sys_mode in (state.MODE_RUNNING, state.MODE_ALARM), \
        "低 SH 不停机，保持运行或进 ALARM"
    assert state.supervisor_interlocks["exv_target_pct"] == 0.0


def test_18_startup_武装表():
    """扩展：STARTUP 不判定吸气低压（§8.3.5 武装表）"""
    state.sys_mode = state.MODE_STARTUP
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_SUCTION_PSI] = 1.0  # < LP_MIN(2.5)
    
    supervisor.tick()
    # LP 规则内部检查 mode != RUNNING → 条件判 False
    assert "LOW_PRESSURE_FAULT" not in supervisor._rule_timers


def test_19_quality_bad_delay():
    """扩展：quality 与报警关系 —— 排气压力 BAD 走 BAD 规则延时"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    # 数值超限但 quality=BAD → 数值规则不计时，BAD 规则计时
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    state.sensor_quality[state.IDX_DISCHARGE_PSI] = state.Q_BAD

    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" not in supervisor._rule_timers, \
        "quality BAD 时数值类高压规则不应计时"
    assert "DISCHARGE_PSI_FAULT" in supervisor._rule_timers, \
        "quality BAD 应启动 BAD 规则计时"

    # DISCHARGE_PSI_BAD_DELAY_S = 5s
    advance_time(2000)
    supervisor.tick()
    assert not state.is_fault("DISCHARGE_PSI_FAULT"), "2s < 5s 不应锁存"

    advance_time(3200)
    supervisor.tick()
    assert state.is_fault("DISCHARGE_PSI_FAULT"), "5s 到点应锁存"
    assert state.sys_mode == state.MODE_STOPPING


def test_20_quality_suspect_no_timer():
    """扩展：quality=SUSPECT 时数值规则与 BAD 规则均不计时"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0
    state.sensor_quality[state.IDX_DISCHARGE_PSI] = state.Q_SUSPECT

    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" not in supervisor._rule_timers
    assert "DISCHARGE_PSI_FAULT" not in supervisor._rule_timers


def test_21_quality_bad_recover_clears_timer():
    """扩展：BAD → 恢复 Q_OK 时 BAD 规则计时器清零（§8.3.4 计时清零）"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_quality[state.IDX_SUCTION_PSI] = state.Q_BAD

    supervisor.tick()
    assert "SUCTION_PSI_FAULT" in supervisor._rule_timers

    advance_time(3000)                      # < SUCTION_PSI_BAD_DELAY_S(10s)
    state.sensor_quality[state.IDX_SUCTION_PSI] = state.Q_OK
    state.sensor_checked_ms[state.IDX_SUCTION_PSI] = ticks_ms()
    supervisor.tick()
    assert "SUCTION_PSI_FAULT" not in supervisor._rule_timers, "恢复应清零计时器"
    assert not state.is_fault("SUCTION_PSI_FAULT")


def test_22_reset_rejected_condition_persists():
    """扩展：复位后条件仍在 → 重走完整延时再次锁存（§8.3.6）"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_checked[state.IDX_DISCHARGE_PSI] = 25.0

    supervisor.tick()
    advance_time(3100)
    supervisor.tick()
    assert state.is_fault("HIGH_PRESSURE_FAULT")
    advance_time(16000)
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP

    # 条件仍在（压力未降）→ 复位受理但立即重新计时
    state.fault_reset_request = True
    supervisor.tick()
    assert not state.is_fault("HIGH_PRESSURE_FAULT"), "复位应先清除锁存位"

    # 重新判定：条件仍在 → 重走完整 3s 延时
    supervisor.tick()
    assert "HIGH_PRESSURE_FAULT" in supervisor._rule_timers, "条件仍在应重新计时"
    advance_time(1000)
    supervisor.tick()
    assert not state.is_fault("HIGH_PRESSURE_FAULT"), "1s 未到点不应立即锁存"
    advance_time(2500)
    supervisor.tick()
    assert state.is_fault("HIGH_PRESSURE_FAULT"), "完整延时后应再次锁存"


def test_23_normal_stop_sequence():
    """扩展：正常停机时序 8.3.2（ENABLE 1->0 先机后风）

    Step5b 修订：EXV 立即关闭 → 延时槽 COMP_STOP_DELAY_SLOT 后停压缩机
    → 延时槽 FAN_STOP_DELAY_SLOT 后停风机，与 PLAN.md §5a 口径一致。
    """
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    supervisor.tick()

    pp = state.protect_params
    comp_stop_ms = pp["delay_slots"][pp["COMP_STOP_DELAY_SLOT"]] * 1000
    fan_stop_ms = pp["delay_slots"][pp["FAN_STOP_DELAY_SLOT"]] * 1000

    # ENABLE 1 -> 0
    state.state_data[state.ST_SYSTEM_ENABLE] = 0
    supervisor.tick()
    assert state.sys_mode == state.MODE_STOPPING
    assert supervisor._stopping_is_fault is False, "应为正常停机"
    il = state.supervisor_interlocks
    # N1: EXV 立即关闭，压缩机/风机按延时槽停止
    assert il["exv_target_pct"] == 0.0, "N1: EXV 立即关闭"
    assert il["comp_enable"] is True, "N1: 压缩机尚未到停机延时"

    # N2: 压缩机延时到点后停压缩机
    advance_time(comp_stop_ms + 100)
    supervisor.tick()
    assert il["comp_enable"] is False, "N2: 压缩机停"
    assert il["evap_fan_enable"] is True, "N2: 风机仍在运行"

    # N3: 风机延时到点后停风机 + 进 SAFE_STOP
    advance_time(fan_stop_ms + 100)
    supervisor.tick()
    assert il["evap_fan_enable"] is False, "N3: 蒸发风机停"
    assert il["cond_fan_enable"] is False, "N3: 冷凝风机停"
    assert state.sys_mode == state.MODE_SAFE_STOP, "N5: 序列完成"

    # 正常停机不锁存故障 → error_reg 全正常
    assert state.error_reg == 0xFFFFFFFF, "正常停机不应锁存故障位"


def test_24_current_bad_action_configurable():
    """扩展：电流传感器 BAD 可配动作（CURRENT_BAD_ACTION 0/1）"""
    # action=0：仅告警，不停机、不锁存
    state.protect_params["CURRENT_BAD_ACTION"] = 0
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_quality[state.IDX_CURRENT] = state.Q_BAD

    supervisor.tick()
    advance_time(5200)                      # CURRENT_BAD_DELAY_S = 5s
    supervisor.tick()
    assert state.is_fault("CURRENT_SENSOR_FAULT"), "应置告警位"
    assert state.sys_mode != state.MODE_STOPPING, "action=0 不应停机"

    # 条件消失 → 非锁存位自动清除
    state.sensor_quality[state.IDX_CURRENT] = state.Q_OK
    state.sensor_checked_ms[state.IDX_CURRENT] = ticks_ms()
    supervisor.tick()
    assert not state.is_fault("CURRENT_SENSOR_FAULT"), "action=0 条件消失应自动清除"

    # action=1：延时停机 + 锁存
    reset_state()
    state.protect_params["CURRENT_BAD_ACTION"] = 1
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    state.sensor_quality[state.IDX_CURRENT] = state.Q_BAD

    supervisor.tick()
    advance_time(5200)
    supervisor.tick()
    assert state.is_fault("CURRENT_SENSOR_FAULT")
    assert state.sys_mode == state.MODE_STOPPING, "action=1 应停机"

    state.protect_params["CURRENT_BAD_ACTION"] = 0   # 还原默认


def test_25_sensor_heartbeat_stale():
    """扩展：软看门狗 —— Sensor 协程停更 → 全通道超龄延时停机"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1
    supervisor.tick()

    # 停更：只推进时间，不刷新 sensor_checked_ms
    # SENSOR_STALE_DELAY_S = 3s，需超过该阈值触发心跳规则
    for i in range(10):
        state.sensor_checked_ms[i] = ticks_ms()  # 先设为当前时间
    
    _mock_now[0] += 3500  # 超过 SENSOR_STALE_DELAY_S(3s)
    supervisor.tick()
    
    # 检查 min_age 是否触发（min_age > SENSOR_STALE_DELAY_S * 1000）
    min_age = min(state.sensor_age_ms(i) for i in range(10))
    assert min_age > 3000, "所有通道应超龄"
    assert "SENSOR_STALE_FAULT" in supervisor._rule_timers, "超龄应启动计时"

    _mock_now[0] += 3200  # 自计时启动起再超过 SENSOR_STALE_DELAY_S(3s)
    supervisor.tick()
    assert state.is_fault("SENSOR_STALE_FAULT"), "超龄延时到点应锁存"
    assert state.sys_mode == state.MODE_STOPPING


def test_26_no_blocking_in_event_loop():
    """扩展：supervisor_loop 无阻塞 sleep，tick 内无 await/time.sleep"""
    import inspect
    src_tick = inspect.getsource(supervisor.tick)
    # tick() 内不应有阻塞 time.sleep（asyncio.sleep_ms 仅在 loop 中）
    assert "time.sleep(" not in src_tick, "tick() 内不得出现 time.sleep() 调用"
    assert "await" not in src_tick, "tick() 必须是同步函数"

    src_loop = inspect.getsource(supervisor.supervisor_loop)
    assert "asyncio.sleep_ms" in src_loop or "sleep_ms" in src_loop, \
        "loop 必须用 asyncio.sleep_ms 让出"
    assert "time.sleep(" not in src_loop, "loop 不得用阻塞 time.sleep() 调用"

    # 所有规则条件函数均为纯函数（无 sleep / await）
    for rule in rules.RULES:
        cond_fn, clear_fn = rule[6], rule[7]
        for fn in (cond_fn, clear_fn):
            src = inspect.getsource(fn)
            assert "sleep" not in src and "await" not in src, \
                "规则函数 %s 必须为纯同步函数" % fn.__name__


def test_27_error_reg_single_writer():
    """扩展：error_reg 单一写者 —— Alarm/ 之外无模块直接写 set_fault/clear_fault"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 检查所有设备/传感器/显示/板级模块不得包含 set_fault/clear_fault
    for subdir in ["EXV", "Sensor", "Display_Touch", "Compressor",
                    "Evaporator", "Condenser", "Board", "HAL"]:
        dir_path = os.path.join(root, subdir)
        if not os.path.exists(dir_path):
            continue
        for fname in os.listdir(dir_path):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dir_path, fname)
            with open(fpath, "r") as f:
                content = f.read()
            
            # state.py 只定义 set_fault/clear_fault，不算调用者
            if fname == "state.py":
                continue
            
            assert "state.set_fault" not in content and "state.clear_fault" not in content, \
                "%s/%s 违反单一写者原则（包含 state.set_fault/clear_fault 调用）" % (subdir, fname)


def test_28_exv_fault_chain_via_supervisor():
    """EXV 故障完整链路：ST_VALVE_FAULT 事件 → supervisor 延时 → error_reg 锁存 → 手动复位"""
    state.sys_mode = state.MODE_RUNNING
    state.state_data[state.ST_SYSTEM_ENABLE] = 1

    # 模拟 EXV 上报 ST_VALVE_FAULT 事件（EXV.py:check_health / Valve_CRTL.py）
    state.state_data[state.ST_VALVE_FAULT] = 1

    # 第一拍：启动计时
    supervisor.tick()
    assert "EXV_FAULT" in supervisor._rule_timers, "ST_VALVE_FAULT=1 应启动 EXV_FAULT 延时计时"
    assert not state.is_fault("EXV_FAULT"), "未到延时不应锁存 error_reg"

    # EXV_FAULT_DELAY_S = 3.0s，推进 2s → 不应锁存
    advance_time(2000)
    supervisor.tick()
    assert not state.is_fault("EXV_FAULT"), "2s < 3s，不应锁存"
    assert state.sys_mode == state.MODE_RUNNING, "未到延时不应停机"

    # 推进至 3.1s → 到点，应锁存 + 故障停机
    advance_time(1100)
    supervisor.tick()
    assert state.is_fault("EXV_FAULT"), "延时到点应锁存 EXV_FAULT (error_reg bit0)"
    assert state.sys_mode == state.MODE_STOPPING, "EXV_FAULT 应触发故障停机序列"

    # 推进停机序列到 SAFE_STOP
    advance_time(16000)
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP

    # 条件恢复（EXV 硬件修好了），但锁存位未复位 → 不得自动启动
    state.state_data[state.ST_VALVE_FAULT] = 0
    supervisor.tick()
    assert state.sys_mode == state.MODE_SAFE_STOP, "锁存未复位不得自动启动"
    assert state.is_fault("EXV_FAULT"), "锁存位未经手动复位不得自动清除"

    # 手动复位 → 条件已消失 → 位保持清除 → 允许 STARTUP
    state.fault_reset_request = True
    supervisor.tick()
    assert not state.is_fault("EXV_FAULT"), "手动复位应清除 EXV_FAULT 锁存"

    supervisor.tick()
    assert state.sys_mode == state.MODE_STARTUP, "复位且条件消失后应允许 STARTUP"


def test_29_exv_modules_no_direct_error_reg():
    """EXV 模块不得直接写 error_reg：静态扫描 EXV/ 目录全部 .py 文件"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exv_dir = os.path.join(root, "EXV")
    
    violations = []
    for fname in os.listdir(exv_dir):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(exv_dir, fname)
        with open(fpath, "r") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                # 跳过注释行
                if stripped.startswith("#"):
                    continue
                if "state.set_fault" in line or "state.clear_fault" in line:
                    violations.append("%s:%d: %s" % (fname, lineno, stripped))
    
    assert len(violations) == 0, \
        "EXV/ 仍有直接写 error_reg 的代码:\n" + "\n".join(violations)


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Step3 Verification Test Suite")
    print("=" * 60)
    
    # 15 项必测 + 3 项扩展
    test("正常状态", test_01_normal_state)
    test("单个报警产生", test_02_single_alarm_trigger)
    test("报警未达到延时时间", test_03_alarm_before_delay)
    test("报警达到延时时间", test_04_alarm_delay_reached)
    test("报警恢复", test_05_alarm_recovery)
    test("报警计时器正确清零", test_06_alarm_timer_clear)
    test("多个报警同时出现", test_07_multiple_alarms)
    test("SAFE_STOP 触发", test_08_safe_stop_trigger)
    test("SAFE_STOP 后恢复输入但未手动复位", test_09_safe_stop_no_auto_restart)
    test("手动复位", test_10_manual_reset)
    test("手动复位条件不满足时拒绝", test_11_reset_condition_not_met)
    test("重复报警/报警抖动", test_12_alarm_flapping)
    test("ticks_ms 回绕时的计时行为", test_13_ticks_wraparound)
    test("Supervisor 不阻塞 uasyncio 主循环", test_14_supervisor_nonblocking)
    test("Step3 不破坏 Step2 quality/fault 行为", test_15_step3_no_break_step2)
    
    # 扩展测试
    test("启动时序 S0-S6 全流程（有反馈）", test_16_startup_sequence_full)
    test("启动步超时 → 锁存 + SAFE_STOP（禁自动重启）", test_16b_startup_timeout_failsafe)
    test("低过热度报警不停机", test_17_low_superheat_no_stop)
    test("STARTUP 不判定吸气低压（武装表）", test_18_startup_武装表)
    test("quality=BAD 走 BAD 规则延时", test_19_quality_bad_delay)
    test("quality=SUSPECT 不计时", test_20_quality_suspect_no_timer)
    test("BAD 恢复 Q_OK 时计时器清零", test_21_quality_bad_recover_clears_timer)
    test("复位后条件仍在 → 重走完整延时", test_22_reset_rejected_condition_persists)
    test("正常停机时序（ENABLE 1->0 先机后风）", test_23_normal_stop_sequence)
    test("电流传感器 BAD 可配动作", test_24_current_bad_action_configurable)
    test("软看门狗：Sensor 协程停更 → 全通道超龄停机", test_25_sensor_heartbeat_stale)
    test("supervisor_loop 无阻塞", test_26_no_blocking_in_event_loop)
    test("error_reg 单一写者（全模块扫描，无豁免）", test_27_error_reg_single_writer)
    test("EXV 故障完整链路（事件→延时→锁存→复位）", test_28_exv_fault_chain_via_supervisor)
    test("EXV 模块无直接 error_reg 写入（静态扫描）", test_29_exv_modules_no_direct_error_reg)
    
    print("\n" + "=" * 60)
    print("Test Summary: %d total, %d passed, %d failed" % (
        _test_count, _pass_count, _fail_count))
    print("=" * 60)
    
    sys.exit(0 if _fail_count == 0 else 1)

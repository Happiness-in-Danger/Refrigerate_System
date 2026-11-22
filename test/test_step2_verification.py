#!/usr/bin/env python3
"""
Step2 verification test suite
Tests all required DoD items without needing hardware
"""

import sys
import os
import time as _time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock MicroPython time functions
_start_ticks = int(_time.time() * 1000)

def ticks_ms():
    return int(_time.time() * 1000) - _start_ticks

def ticks_diff(a, b):
    return a - b

# Replace time module with MicroPython-compatible version
class MockTime:
    ticks_ms = staticmethod(ticks_ms)
    ticks_diff = staticmethod(ticks_diff)
    sleep = _time.sleep

sys.modules['time'] = MockTime()

# Mock MicroPython modules for PC testing
class MockPin:
    def __init__(self, name):
        self.name = name

class MockADC:
    def __init__(self, pin):
        self.pin = pin
        self._value = 32768  # Mid-range
    
    def read(self):
        return self._value
    
    def set_value(self, val):
        self._value = val

class MockUART:
    def __init__(self, *args, **kwargs):
        pass

class MockSPI:
    def __init__(self, *args, **kwargs):
        pass

class MockADCDirect:
    @staticmethod
    def read_pc2_c():
        return 32768
    
    @staticmethod
    def read_pc3_c():
        return 32768

class MockMachine:
    class SoftSPI:
        def __init__(self, *args, **kwargs):
            pass

sys.modules['pyb'] = type(sys)('pyb')
sys.modules['pyb'].Pin = MockPin
sys.modules['pyb'].ADC = MockADC
sys.modules['pyb'].UART = MockUART
sys.modules['pyb'].SPI = MockSPI
sys.modules['machine'] = MockMachine()
sys.modules['adc_direct'] = MockADCDirect()
sys.modules['uasyncio'] = type(sys)('uasyncio')

# Now import actual modules
import Board.state as state
from Sensor.validate import validate

print("=" * 60)
print("Step2 Verification Test Suite")
print("=" * 60)

# Test counters
passed = 0
failed = 0

def test(name, condition, details=""):
    global passed, failed
    if condition:
        print(f"[PASS] {name}")
        passed += 1
    else:
        print(f"[FAIL] {name}")
        if details:
            print(f"       {details}")
        failed += 1

print("\n--- Test 1: P0-1 Fix - set_sensor_fault/clear_sensor_fault with global ---")
try:
    initial = state.sensor_fault_reg
    state.set_sensor_fault("SUCTION_T_FAULT")
    after_set = state.sensor_fault_reg
    test("set_sensor_fault executes without UnboundLocalError", 
         after_set != initial,
         f"Before: 0x{initial:04X}, After: 0x{after_set:04X}")
    
    state.clear_sensor_fault("SUCTION_T_FAULT")
    after_clear = state.sensor_fault_reg
    test("clear_sensor_fault executes without UnboundLocalError",
         after_clear == initial,
         f"Restored: 0x{after_clear:04X}")
except Exception as e:
    test("set_sensor_fault/clear_sensor_fault without errors", False, str(e))

print("\n--- Test 2: P0-2 Fix - Current sensor sampling prevents 0A valid issue ---")
try:
    # Reset and ensure current starts at 999 (unsampled)
    state.sensor_data[state.IDX_CURRENT] = 999
    state.sensor_quality = [state.Q_BAD] * 10
    state.sensor_checked_ms = [0] * 10
    
    # Run validate
    validate()
    
    # Check that IDX_CURRENT is marked as Q_BAD when value is 999
    current_quality = state.sensor_quality[state.IDX_CURRENT]
    
    test("Current sensor initial state is Q_BAD (prevents 0A valid issue)",
         current_quality == state.Q_BAD,
         f"Quality: {current_quality}, Value: {state.sensor_data[state.IDX_CURRENT]}")
    
    # Now simulate actual sampling with valid current
    state.sensor_data[state.IDX_CURRENT] = 5.5
    validate()
    current_quality_after = state.sensor_quality[state.IDX_CURRENT]
    test("Current sensor can transition to Q_OK with valid data",
         current_quality_after == state.Q_OK,
         f"Quality: {current_quality_after}")
    
except Exception as e:
    test("Current sensor validation", False, str(e))

print("\n--- Test 3: Quality initialization is Q_BAD ---")
# Reset and check initial state
import importlib
importlib.reload(state)
test("All sensor_quality initialized to Q_BAD",
     all(q == state.Q_BAD for q in state.sensor_quality),
     f"sensor_quality: {state.sensor_quality}")

print("\n--- Test 4: validate() can execute in finally block ---")
try:
    # Simulate reads() with exception and finally block
    state.sensor_data = [999] * 10
    
    # This mimics the finally block in reads()
    state.update_all_sensor_faults()
    validate()
    
    # Check that fault_reg was updated
    test("update_all_sensor_faults() executes in finally",
         state.sensor_fault_reg != 0xFFFF,
         f"sensor_fault_reg: 0x{state.sensor_fault_reg:04X}")
    
    # Check that all qualities are BAD (due to 999)
    test("validate() executes in finally",
         all(q == state.Q_BAD for q in state.sensor_quality),
         f"All qualities BAD due to 999 sentinel")
    
except Exception as e:
    test("validate() in finally block", False, str(e))

print("\n--- Test 5: Quality transitions work correctly ---")
try:
    # Reset state properly
    state.sensor_data = [20.0] * 10
    state.sensor_data[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_data[state.IDX_DISCHARGE_PSI] = 15.0
    state.sensor_data[state.IDX_CURRENT] = 10.0
    state.sensor_quality = [state.Q_BAD] * 10
    state.sensor_checked_ms = [0] * 10
    
    # Import validate to reset its internal state
    from Sensor.validate import validate as val_func
    import Sensor.validate as val_mod
    val_mod._prev_value = [None] * 10
    val_mod._suspect_cnt = [0] * 10
    
    validate()
    
    test("Valid data transitions to Q_OK",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_OK,
         f"SUCTION_T quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Test 999 sentinel
    state.sensor_data[state.IDX_SUCTION_T] = 999
    validate()
    
    test("999 sentinel causes Q_BAD",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_BAD,
         f"SUCTION_T quality after 999: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Test out of range
    state.sensor_data[state.IDX_SUCTION_T] = 200.0  # > 150 max
    validate()
    
    test("Out of range causes Q_BAD",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_BAD,
         f"SUCTION_T quality after 200C: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
except Exception as e:
    test("Quality transitions", False, str(e))

print("\n--- Test 6: fault_reg updates correctly ---")
try:
    # All good values
    state.sensor_data = [20.0] * 10
    state.sensor_data[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_data[state.IDX_DISCHARGE_PSI] = 15.0
    state.sensor_data[state.IDX_CURRENT] = 10.0
    
    state.update_all_sensor_faults()
    
    test("fault_reg all normal with good data",
         state.sensor_fault_reg == 0xFFFF,
         f"fault_reg: 0x{state.sensor_fault_reg:04X}")
    
    # One sensor bad
    state.sensor_data[state.IDX_SUCTION_T] = 999
    state.update_all_sensor_faults()
    
    test("fault_reg marks SUCTION_T fault",
         state.sensor_fault_reg != 0xFFFF,
         f"fault_reg: 0x{state.sensor_fault_reg:04X}")
    
    bit_cleared = not (state.sensor_fault_reg & (1 << 0))
    test("SUCTION_T_FAULT bit correctly cleared",
         bit_cleared,
         f"Bit 0 state: {bit_cleared}")
    
except Exception as e:
    test("fault_reg updates", False, str(e))

print("\n--- Test 7: PID gate logic (sensor_checked access) ---")
try:
    # Reset state
    import Sensor.validate as val_mod
    val_mod._prev_value = [None] * 10
    val_mod._suspect_cnt = [0] * 10
    state.sensor_quality = [state.Q_BAD] * 10
    state.sensor_checked_ms = [0] * 10
    
    # Setup good SH data
    state.sensor_data[state.IDX_SUCTION_T] = 20.0
    state.sensor_data[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_data[state.IDX_SUPERHEAT] = 5.0
    validate()
    
    sh_q = state.sensor_quality[state.IDX_SUPERHEAT]
    sh_age = state.sensor_age_ms(state.IDX_SUPERHEAT)
    
    should_run_pid = (sh_q == state.Q_OK and sh_age <= 1000)
    
    test("PID gate opens with Q_OK and fresh data",
         should_run_pid,
         f"Quality: {sh_q}, Age: {sh_age}ms")
    
    # Make data stale by waiting
    _time.sleep(1.1)
    sh_age_old = state.sensor_age_ms(state.IDX_SUPERHEAT)
    should_skip_pid = (sh_age_old > 1000)
    
    test("PID gate closes with stale data",
         should_skip_pid,
         f"Age after 1.1s: {sh_age_old}ms")
    
except Exception as e:
    test("PID gate logic", False, str(e))

print("\n--- Test 8: C7 Fix - Quality locking (no oscillation) ---")
try:
    # Reset state completely
    import Sensor.validate as val_mod
    val_mod._prev_value = [None] * 10
    val_mod._suspect_cnt = [0] * 10
    state.sensor_quality = [state.Q_BAD] * 10
    state.sensor_checked_ms = [0] * 10
    
    # Start with good value
    state.sensor_data[state.IDX_SUCTION_T] = 20.0
    validate()
    test("Initial Q_OK", state.sensor_quality[state.IDX_SUCTION_T] == state.Q_OK,
         f"Quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Step6/B6: RATE_LIMITS 已按实测 800ms 周期重标（温度 20K/period），
    # 故此处用 >20K 的毛刺才能触发 SUSPECT（原 +10K 对应旧 200ms 标定）。
    # First spike (SUSPECT)
    state.sensor_data[state.IDX_SUCTION_T] = 45.0  # +25K spike
    validate()
    test("First spike -> Q_SUSPECT",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_SUSPECT,
         f"Quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Second spike (should go to BAD and LOCK)
    state.sensor_data[state.IDX_SUCTION_T] = 70.0  # Another spike
    validate()
    test("Second spike -> Q_BAD (locked)",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_BAD,
         f"Quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Third reading still bad (should STAY BAD, not oscillate back)
    state.sensor_data[state.IDX_SUCTION_T] = 95.0
    validate()
    test("Third spike -> still Q_BAD (no oscillation)",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_BAD,
         f"Quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
    # Return to normal should recover
    state.sensor_data[state.IDX_SUCTION_T] = 20.0
    validate()
    test("Return to normal -> Q_OK (can recover)",
         state.sensor_quality[state.IDX_SUCTION_T] == state.Q_OK,
         f"Quality: {state.sensor_quality[state.IDX_SUCTION_T]}")
    
except Exception as e:
    test("Quality locking (C7 fix)", False, str(e))

print("\n--- Test 9: Derived channel uses max(parent) ---")
try:
    # Reset state
    import Sensor.validate as val_mod
    val_mod._prev_value = [None] * 10
    val_mod._suspect_cnt = [0] * 10
    state.sensor_quality = [state.Q_BAD] * 10
    state.sensor_checked_ms = [0] * 10
    
    # Both parents OK
    state.sensor_data[state.IDX_SUCTION_T] = 20.0
    state.sensor_data[state.IDX_SUCTION_PSI] = 5.0
    state.sensor_data[state.IDX_SUPERHEAT] = 5.0
    validate()
    
    test("SH quality = OK when both parents OK",
         state.sensor_quality[state.IDX_SUPERHEAT] == state.Q_OK,
         f"SH quality: {state.sensor_quality[state.IDX_SUPERHEAT]}")
    
    # One parent SUSPECT（Step6/B6: >20K 毛刺）
    state.sensor_data[state.IDX_SUCTION_T] = 45.0  # Spike -> SUSPECT
    validate()
    
    sh_q_with_suspect = state.sensor_quality[state.IDX_SUPERHEAT]
    test("SH inherits SUSPECT from parent",
         sh_q_with_suspect >= state.Q_SUSPECT,
         f"SH quality: {sh_q_with_suspect}")
    
    # One parent BAD
    state.sensor_data[state.IDX_SUCTION_T] = 999
    validate()
    
    test("SH inherits BAD from parent",
         state.sensor_quality[state.IDX_SUPERHEAT] == state.Q_BAD,
         f"SH quality: {state.sensor_quality[state.IDX_SUPERHEAT]}")
    
except Exception as e:
    test("Derived channel quality inheritance", False, str(e))

print("\n--- Test 10: No new coroutines created ---")
# This is a code inspection test - check validate.py source
try:
    with open('/Users/zhi/Project/Refrigerate_System/Sensor/validate.py', 'r') as f:
        validate_src = f.read()
    
    with open('/Users/zhi/Project/Refrigerate_System/Sensor/read_sensors.py', 'r') as f:
        read_sensors_src = f.read()
    
    # Check for async def
    # Step2 requirement: validate() itself must NOT be async
    # Step4 addition: reads_async() is allowed (new function, doesn't break Step2)
    has_async_in_validate = 'async def' in validate_src
    validate_func_is_async = 'async def validate' in validate_src
    
    test("validate() is not async (Step2 requirement)",
         not validate_func_is_async,
         f"validate.py has 'async def validate': {validate_func_is_async}")
    
    test("validate.py has no async functions",
         not has_async_in_validate,
         f"validate.py has any async: {has_async_in_validate}")
except Exception as e:
    test("No new coroutines created", False, str(e))

print("\n" + "=" * 60)
print(f"Test Results: {passed} passed, {failed} failed")
print("=" * 60)

if failed == 0:
    print("\nStep2 DoD: PASS")
    sys.exit(0)
else:
    print("\nStep2 DoD: FAIL")
    sys.exit(1)

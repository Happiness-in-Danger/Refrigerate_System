#state.py
"""
全局共享状态 —— 所有协程/模块通过 import 本模块读写,
禁止在各文件里各自维护同名局部变量。
"""

# 传感器原始数据(9元素,由 Sensor 协程刷新)
# [suction_t, discharge_t, inlet_t, outlet_t, liquid_t,
#  suction_psi, discharge_psi, superheat, E_value] curent？
sensor_data = [0] * 9

# 运行状态数据(9元素,由  协程刷新)
# []
state_data = [0]*9

# 运行时控制参数(软件默认值启动,屏幕连接后由 param_sync 覆盖同步)
control_params = {
    "Kp": 0.6, "Ki": 0.12, "Kd": 3.0,
    "setpoint": 5.0, "error_threshold": 2.0,
    "deadband": 0.2, "max_delta": 8.0,
    "aggr_mode": "togoal",
}

# 故障点位(16元素,对应 memory 里已扩展的 index 分配)
error_point = [0] * 16

# 全局运行状态(电机/阀门/故障标志等)
global_state = {
    'motor_speed': 0,
    'valve_pos': 0,
    'sensor_fault': False,
}
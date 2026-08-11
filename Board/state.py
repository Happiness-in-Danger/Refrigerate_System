#state.py
"""
全局共享状态 —— 所有协程/模块通过 import 本模块读写,
禁止在各文件里各自维护同名局部变量。
"""

# ────────────────────────────────────────────────────────────
# 传感器原始数据 (10 元素, 由 Sensor 协程 read_sensors.reads() 刷新)
# 下标含义 —— 与 Sensor/read_sensors.py 中的赋值顺序严格一致
# ────────────────────────────────────────────────────────────
IDX_SUCTION_T     = 0   # 回气温度 (PT100)
IDX_DISCHARGE_T   = 1   # 排气温度 (PT100)
IDX_INLET_T       = 2   # 进液温度 (PT100)
IDX_OUTLET_T      = 3   # 出液温度 (PT100)
IDX_LIQUID_T      = 4   # 液管温度 (PT100)
IDX_SUCTION_PSI   = 5   # 低压压力 (SPI 压力传感器)
IDX_DISCHARGE_PSI = 6   # 高压压力 (SPI 压力传感器)
IDX_SUPERHEAT     = 7   # 回气过热度 SH (suction_t - 饱和温度)
IDX_E_VALUE       = 8   # 查表所得焓值 E (Extensions.read_r134a.read_E)
IDX_CURRENT       = 9   # 整机电流 (board.adc_current 为主采样，adc_backup 为备用/冗余采集)

sensor_data = [0] * 10

# 运行状态数据(9元素,由  协程刷新)
ST_MOTOR_SPEED        = 0    # 压缩机转速指令/反馈，单位 r/min
 
# EXV 阀门 —— 只读镜像，供 Display / Modbus 等模块查询当前开度。
# EXV.py 内部的 pending_delta / valve_busy 生产者-消费者状态仍由
# EXV 模块自己维护，不在这里暴露；EXV 每次动作成功后应把最新位置
# 同步写回 state.state_data[ST_VALVE_POS] / [ST_VALVE_POS_STEPS]。
ST_VALVE_POS          = 1    # 当前开度 (百分比 0.0~100.0)
ST_VALVE_POS_STEPS    = 2    # 当前开度 (步数 0~500)
ST_VALVE_FAULT        = 3    # EXV/TMC2209 驱动器故障 (check_health() 失败)，0/1
 
# 风机 —— 蒸发器/冷凝器均为 DShot 控制，转速指令/反馈格式与
# 压缩机一致，统一用 r/min
ST_EVAP_FAN_SPEED_CMD = 4    # 蒸发器风机 DShot 转速指令 (r/min)
ST_EVAP_FAN_RPM       = 5    # 蒸发器风机转速反馈 (r/min)
ST_EVAP_FAN_FAULT     = 6    # 蒸发器风机转速异常/堵转标志，0/1
 
ST_COND_FAN_SPEED_CMD = 7    # 冷凝器风机 DShot 转速指令 (r/min)
ST_COND_FAN_RPM       = 8    # 冷凝器风机转速反馈 (r/min)
ST_COND_FAN_FAULT     = 9    # 冷凝器风机转速异常/堵转标志，0/1
 
ST_SENSOR_FAULT       = 10   # 任一传感器读数命中故障哨兵值时置位，0/1
 
state_data = [0] * 11


# ────────────────────────────────────────────────────────────
# 传感器故障寄存器 —— 16位, 每个传感器通道单独占一位
# 1 = 正常, 0 = 异常, 全部正常时 sensor_fault_reg == 0xFFFF
#
# 只覆盖"直接测量"的传感器 (5路PT100 + 2路压力 + 1路电流)，
# 过热度/焓值是计算值，不在这里占位，异常与否由上游传感器位反映。
# 8~15 位预留，以后加传感器往后排即可。
# ────────────────────────────────────────────────────────────
SENSOR_FAULT_BITS = {
    "SUCTION_T_FAULT":     (0, IDX_SUCTION_T),
    "DISCHARGE_T_FAULT":   (1, IDX_DISCHARGE_T),
    "INLET_T_FAULT":       (2, IDX_INLET_T),
    "OUTLET_T_FAULT":      (3, IDX_OUTLET_T),
    "LIQUID_T_FAULT":      (4, IDX_LIQUID_T),
    "SUCTION_PSI_FAULT":   (5, IDX_SUCTION_PSI),
    "DISCHARGE_PSI_FAULT": (6, IDX_DISCHARGE_PSI),
    "CURRENT_FAULT":       (7, IDX_CURRENT),
}

 
sensor_fault_reg = 0xFFFF   # 16位寄存器；bit=1 正常，bit=0 异常
 
def set_sensor_fault(name):
    bit, _ = SENSOR_FAULT_BITS[name]
    sensor_fault_reg &= ~(1 << bit) & 0xFFFF

def clear_sensor_fault(name):
    bit, _ = SENSOR_FAULT_BITS[name]
    sensor_fault_reg |= (1 << bit)

def update_all_sensor_faults(fault_value=999):
    """在 reads() 里调用一次，自动根据 sensor_data 当前值刷新所有故障位"""
    for name, (bit, idx) in SENSOR_FAULT_BITS.items():
        if sensor_data[idx] == fault_value:
            set_sensor_fault(name)
        else:
            clear_sensor_fault(name)

# 运行时控制参数(软件默认值启动,屏幕连接后由 param_sync 覆盖同步)

control_params = {
    "Kp": 0.6, "Ki": 0.12, "Kd": 3.0,
    "setpoint": 5.0, "error_threshold": 2.0,
    "deadband": 0.2, "max_delta": 8.0,
    "aggr_mode": "togoal",
    "tau": 3.0, "T_goal": 5.0, 
}
def load_control_params(path="config.json"):
    """开机调用：用config.json覆盖默认值，缺的key保留默认值不报错"""
    try:
        import json
        with open(path, "r") as f:
            saved = json.load(f)
        control_params.update({k: v for k, v in saved.items() if k in control_params})
    except Exception as e:
        print("[state] config加载失败，使用默认参数:", e)
    return control_params

def save_control_params(path="config.json"):
    """把当前control_params落盘"""
    try:
        import json
        with open(path, "w") as f:
            json.dump(control_params, f)
        return True
    except Exception as e:
        print("[state] config保存失败:", e)
        return False

# ────────────────────────────────────────────────────────────
# 系统级故障寄存器 —— 16位, 对应 Modbus 离散输入 1x, 地址 0~15, FC02
# 每一位代表一路故障：1 = 正常，0 = 异常
# 全部正常时 error_reg == 0xFFFF；哪路故障就把对应位翻转成 0。
#
# 传感器通道本身的故障已经拆到上面的 sensor_fault_reg 里了，
# 这里只放"系统/保护"级别的故障 (EXV、风机、压缩机、通讯、
# 高低压保护等)，不再有笼统的 SENSOR_FAULT 位。
#
# ERROR_BITS 是我按项目里已有模块整理的草稿，目前代码里还没有
# 任何地方真正调用 set_fault()/clear_fault()，请按需增删/改名后
# 再统一接入各模块的故障判断逻辑。
# ────────────────────────────────────────────────────────────
ERROR_BITS = {
    "EXV_FAULT":              0,   # EXV 归零失败 / TMC2209 驱动器故障 (通信失败/短路/过温/欠压)
    "COMPRESSOR_FAULT":       1,   # 压缩机启动失败/驱动通讯异常 (预留，压缩机驱动方案未定)
    "EVAP_FAN_FAULT":         2,   # 蒸发器风机 (DShot) 转速异常/无反馈
    "COND_FAN_FAULT":         3,   # 冷凝器风机 (DShot) 转速异常/无反馈
    "HIGH_PRESSURE_FAULT":    4,   # 高压保护 (排气压力超限)
    "LOW_PRESSURE_FAULT":     5,   # 低压保护 (吸气压力过低)
    "HIGH_DISCHARGE_TEMP":    6,   # 排气温度过高
    "OVERCURRENT_FAULT":      7,   # 整机电流过流保护
    "LCD_COMM_FAULT":         8,   # 组态屏通讯故障 (Display_Touch UART)
    "MODBUS_COMM_FAULT":      9,   # Modbus 通讯故障 (预留，Modbus 模块未实现)
    "LOW_SUPERHEAT_FAULT":    10,  # 过热度过低，存在液击风险
    "CONFIG_IO_FAULT":        11,  # config.json 读写失败 / 参数同步异常
    # 12~15 暂未分配，预留给后续扩展 (如 EEPROM、外部报警输入等)
}
 
error_reg = 0xFFFF   # 16位寄存器；bit=1 正常，bit=0 异常，全部正常 = 0xFFFF
 
def set_fault(name: str):
    """把 name 对应的位翻转成 0 (异常)"""
    global error_reg
    error_reg &= ~(1 << ERROR_BITS[name]) & 0xFFFF
 
def clear_fault(name: str):
    """把 name 对应的位恢复成 1 (正常)"""
    global error_reg
    error_reg |= (1 << ERROR_BITS[name])
 
def is_fault(name: str) -> bool:
    """查询 name 对应的位是否处于异常 (0)"""
    return not (error_reg & (1 << ERROR_BITS[name]))

# modbus_regs.py
# 统一寄存器堆：所有和屏幕交互的数据都通过这里读写。
# 业务代码（Sensor_Alarm/Exv等协程）只管往这里写/读，不用关心Modbus协议细节。
#
# 约定：
#   - 除非特别说明，所有寄存器都是16位有符号整数（-32768~32767）
#   - 浮点值一律 *100 定点化存储，读出来再 /100 还原
#   - discrete_inputs 用 0/1 表示，对应功能码02

class RegisterMap:
    def __init__(self):
        # ---- 输入寄存器 (只读, 功能码04) ----
        # 0-9   sensor_data[0..9] *100 (含电流)
        # 10-15 预留
        # 16    error_reg
        # 17    sensor_fault_reg
        # 18    valve_pos*100        (state.state_data[ST_VALVE_POS])
        # 19    valve_pos_steps      (state.state_data[ST_VALVE_POS_STEPS])
        # 20    evap_fan_rpm         (state.state_data[ST_EVAP_FAN_RPM])
        # 21    cond_fan_rpm         (state.state_data[ST_COND_FAN_RPM])
        # 22    motor_speed          (state.state_data[ST_MOTOR_SPEED])
        # 23    valve_fault          (state.state_data[ST_VALVE_FAULT])      0/1
        # 24    evap_fan_fault       (state.state_data[ST_EVAP_FAN_FAULT])   0/1
        # 25    cond_fan_fault       (state.state_data[ST_COND_FAN_FAULT])   0/1
        # 26    sensor_fault_summary (state.state_data[ST_SENSOR_FAULT])     0/1
        # 27-31 预留
        self.input_regs = [0] * 32

        # ---- 离散量输入 (只读, 功能码02) ----
        # 地址 0-15 对应 Error_point[0..15] 报警位
        self.discrete_inputs = [0] * 16

        # ---- 保持寄存器 (读写, 功能码03/06/16) ----
        # 地址 0: Kp*100        1: Ki*100         2: Kd*100
        # 地址 3: setpoint*100  4: deadband*100   5: max_delta*100
        # 地址 6: error_threshold*100  7: T_goal*100
        # 地址 8: EXV当前阀位*100 (只读语义，但物理上仍开放写，业务层自己保护)
        # 地址 9: 手动模式开关 (0=自动 1=手动)
        # 地址 10: 手动阀位指令*100
        # 地址 11: aggr_mode 枚举 (0=off 1=proportional 2=togoal)
        # 地址 12: tau*100
        # 地址 13: 系统启动开关 (0=停机 1=运行)
        #          对应 state.state_data[ST_SYSTEM_ENABLE]
        # 地址 14: 压缩机PID Kp*100
        # 地址 15: 压缩机PID Ki*100
        # 地址 16: 压缩机PID Kd*100
        # 地址 17: 压缩机PID setpoint*100
        # 地址 18: 压缩机PID deadband*100
        # 地址 19: 压缩机PID max_delta*100
        # 地址 20: 压缩机PID error_threshold*100
        # 地址 21: 压缩机PID T_goal*100
        # 地址 22: 压缩机PID aggr_mode 枚举
        # 地址 23: 压缩机PID tau*100
        # 地址 24: 蒸发器风机转速指令 (r/min)  对应 state.state_data[ST_EVAP_FAN_SPEED_CMD]
        # 地址 25: 冷凝器风机转速指令 (r/min)  对应 state.state_data[ST_COND_FAN_SPEED_CMD]
        # 地址 24: 蒸发器风机转速指令 (r/min)  对应 state.state_data[ST_EVAP_FAN_SPEED_CMD]
        # 地址 25: 冷凝器风机转速指令 (r/min)  对应 state.state_data[ST_COND_FAN_SPEED_CMD]
        # 地址 26: 压缩机转速下限 (r/min)      对应 state.state_data[ST_MOTOR_SPEED_MIN]
        # 地址 27: 压缩机转速上限 (r/min)      对应 state.state_data[ST_MOTOR_SPEED_MAX]
        # 地址 28: 风机转速下限 (r/min)        对应 state.state_data[ST_FAN_SPEED_MIN]
        # 地址 29: 风机转速上限 (r/min)        对应 state.state_data[ST_FAN_SPEED_MAX]
        # 地址 30-31 预留
        self.holding_regs = [0] * 32

    # ---- 便捷读写方法，带边界检查 ----
    def read_block(self, table, addr, count):
        if addr < 0 or addr + count > len(table):
            return None
        return table[addr:addr + count]

    def write_single(self, table, addr, value):
        if addr < 0 or addr >= len(table):
            return False
        table[addr] = value
        return True

    def write_block(self, table, addr, values):
        if addr < 0 or addr + len(values) > len(table):
            return False
        for i, v in enumerate(values):
            table[addr + i] = v
        return True


# 全局单例，main.py 和 modbus_slave_task 共用同一份
regs = RegisterMap()

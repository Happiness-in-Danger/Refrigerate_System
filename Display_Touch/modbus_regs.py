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
        # 地址 0-8 对应 sensor_data[0..8]:
        #   0 suction_temp  1 discharge_temp  2 inlet_temp  3 outlet_temp
        #   4 liquid_temp   5 suction_psi     6 discharge_psi
        #   7 superheat(SH) 8 E value
        # 地址 9-15 预留
        self.input_regs = [0] * 16

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
        # 地址 11-31 预留
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
# modbus_regs.py
# 统一寄存器堆：所有和屏幕交互的数据都通过这里读写。
# 业务代码（Sensor_Alarm/Exv等协程）只管往这里写/读，不用关心Modbus协议细节。
#
# 约定：
#   - 除非特别说明，所有寄存器都是16位有符号整数（-32768~32767）
#   - 浮点值一律 *100 定点化存储，读出来再 /100 还原
#   - coils / discrete_inputs 用 0/1 表示
#
# 映射基准（2026-09-19 重构）：docs/参考.csv
#   地址约定：报文地址 = 代码索引（0 基，无偏移）；屏上显示地址 = 索引 + 1
#     coils[0-7]                 <-> 0x1-0x8
#     discrete_inputs[0-15]      <-> 1x1-1x16   （error_reg bit0-15 镜像）
#     discrete_inputs[99-104]    <-> 1x100-1x105
#     input_regs / holding_regs  <-> 3x / 4x 各段（见 modbus_slave.py）

class RegisterMap:
    def __init__(self):
        # ---- 线圈 (读写, 功能码01/05/15) ----
        # 0x1-0x8 -> coils[0-7]
        self.coils = [0] * 8

        # ---- 离散量输入 (只读, 功能码02) ----
        # 0-15: error_reg bit0-15 报警位（1=正常，0=故障）
        # 99-104: Driver链路/联锁使能/重试锁定（1x100-1x105）
        # 105-115: 保留（1x106-1x115）
        self.discrete_inputs = [0] * 116

        # ---- 输入寄存器 (只读, 功能码04) ----
        # 3x1-3x223 -> input_regs[0-222]
        self.input_regs = [0] * 223

        # ---- 保持寄存器 (读写, 功能码03/06/16) ----
        # 4x1-4x223 -> holding_regs[0-222]
        self.holding_regs = [0] * 223

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

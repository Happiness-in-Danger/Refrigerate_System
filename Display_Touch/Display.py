# 组态屏 VTc070C334A（A5 5A 协议，UART1，测试方案）
#
# Step7：由 Desplay.py 重命名为 Display.py（D3 拼写修正），函数 desplay→display。
# A8 修复：故障哨兵 999 原样透传，不再 ×100（99900 溢出 u16 → 屏显 343.64）。
#         与 Display_Touch/modbus_slave.py 的 FAULT_SENTINEL 约定一致。
import time
from Board import board
import Board.state as state

FAULT_SENTINEL = 999   # 与 Sensor/read_sensors.py、HAL/ADC_read.py 一致

uart = board.LCD_Uart
addre = board.REGS  # (address,dtype)


def _scale(x):
    """浮点值 ×100 定点化；故障哨兵 999 原样透传（A8）。"""
    if x == FAULT_SENTINEL:
        return FAULT_SENTINEL
    return int(100 * x)


#write
def uart_write(data, regs):
    buf = bytearray()

    for x, (y, t) in zip(data, regs):
        if t == 'u32':
            value = _scale(x) & 0xFFFFFFFF
            buf += bytes([
                0xA5, 0x5A, 0x07, 0x82,
                (y >> 8) & 0xFF, y & 0xFF,
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8)  & 0xFF,
                value & 0xFF,
            ])
        else:
            value = _scale(x) & 0xFFFF
            buf += bytes([
                0xA5, 0x5A, 0x05, 0x82,
                (y >> 8) & 0xFF, y & 0xFF,
                (value >> 8) & 0xFF,
                value & 0xFF,
            ])

    uart.write(buf)

#read
def uart_read(data, address):


    return data
# ------------------ run ------------------
def display():
    data_w = state.sensor_data
    uart_write(data_w, addre)
    # data_r

#组态屏 VTc070C334A
import time
from Board import board
import Board.state as state

uart = board.LCD_Uart
addre = board.REGS #(address,dtype)

#write
def uart_write(data, regs):
    buf = bytearray()
    
    for x, (y, t) in zip(data, regs):
        if t == 'u32':
            value = int(100 * x) & 0xFFFFFFFF
            buf += bytes([
                0xA5, 0x5A, 0x07, 0x82,
                (y >> 8) & 0xFF, y & 0xFF,
                (value >> 24) & 0xFF,
                (value >> 16) & 0xFF,
                (value >> 8)  & 0xFF,
                value & 0xFF,
            ])
        else:
            value = int(100 * x) & 0xFFFF
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
def desplay():
    data_w = state.sensor_data
    uart_write(data_w,addre)
    # data_r
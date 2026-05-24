import time
from pyb import Pin, SPI
from HAL.SPI import SPIBus,SPIDev
# from Sensor.tem_lastV02_STM32 import red_temp,red_temps,read_tem_sensor
from Sensor.AD7124_PT100 import AD7124
from Sensor.Pressure_read import read_psi,psi_trans
from Board import board
from Extensions.read_r134a import read_sat_temp,read_E

data_buf = [0] * 9
def reads():
    tem_data = board.rts.config_pt100_chs()
    for i in range(5):
        data_buf[i] = tem_data[i]
    data_buf[5] = psi_trans(board.adc_direct.read_pc2_c())
    data_buf[6] = psi_trans(board.adc_direct.read_pc3_c())
    data_buf[7] = int(( data_buf[0] - read_sat_temp(data_buf[5]))*100)/100
    data_buf[8] = read_E(data_buf[5],data_buf[0])
    return data_buf #suction discharge inlet outlet lequit suction_psi discharge_psi suction_sh

import time
from pyb import Pin, SPI
from HAL.SPI import SPIBus,SPIDev
from HAL.ADC_sample import SmoothedADC
from Sensor.Pressure_read import read_psi,psi_trans
from Board import board
from Extensions.read_r134a import read_sat_temp,read_E
import Board.state as state


def reads():
    tem_data = board.rts.config_pt100_chs()
    for i in range(5):
        state.sensor_data[i] = tem_data[i]
    state.sensor_data[5] = psi_trans(board.adc_pc2c.SmoothedADC.value())
    state.sensor_data[6] = psi_trans(board.adc_pc3c.SmoothedADC.value())
    state.sensor_data[7] = int(( state.sensor_data[0] - read_sat_temp(state.sensor_data[5]))*100)/100
    state.sensor_data[8] = read_E(state.sensor_data[5],state.sensor_data[0])
    return state.sensor_data #suction discharge inlet outlet lequit suction_psi discharge_psi suction_sh

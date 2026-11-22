import time
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio
from pyb import Pin, SPI
from HAL.SPI import SPIBus,SPIDev
from HAL.ADC_sample import SmoothedADC
from Sensor.ADC_read import read_psi,psi_trans,read_current
from Sensor.AD7124_PT100 import AD7124
from Board import board
from Extensions.read_r134a import read_sat_temp,read_E
import Board.state as state
from Sensor.validate import validate

# ─── AD7124 惰性初始化 ──────────────────────────────────────
# 实例由 Sensor 子系统自建自管（board 只提供总线/片选资源）。
# 初始化失败时 5 路温度置 999
_rts = None
_rts_failed = False

def _get_rts():
    global _rts, _rts_failed
    if _rts is None and not _rts_failed:
        try:
            _rts = AD7124(board.AD7124_spi.spi, board.AD7124_cs.cs)
        except Exception as e:
            print("[Sensor] AD7124 初始化失败，温度通道降级:", e)
            _rts_failed = True
    return _rts


def reads():
    """
    同步版本（保留兼容性），采集一次全部传感器，写入 sensor_data[]；
    无论是否异常都保证调用 update_all_sensor_faults() + validate()，
    确保 sensor_quality / sensor_fault_reg 不会停留在旧状态。
    """
    try:
        rts = _get_rts()
        if rts is not None:
            try:
                tem_data = rts.config_pt100_chs()
            except Exception as e:
                print("[Sensor] AD7124 读取异常，温度通道置 999:", e)
                tem_data = [999] * 5
        else:
            tem_data = [999] * 5

        for i in range(5):
            state.sensor_data[i] = tem_data[i]

        state.sensor_data[5] = psi_trans(board.adc_pc2c.value())
        state.sensor_data[6] = psi_trans(board.adc_pc3c.value())

        # 过热度 SH：查表失败只影响本通道
        try:
            sat_temp = read_sat_temp(state.sensor_data[5])
            sh_raw = state.sensor_data[0] - sat_temp
            state.sensor_data[7] = int(sh_raw * 100 +
                (0.5 if sh_raw >= 0 else -0.5)) / 100
        except Exception as e:
            print("[Sensor] SH 计算异常，单通道置 999:", e)
            state.sensor_data[7] = 999

        # 焓值 E（吸气焓，3x10）：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值（出口焓，3x11）：read_E(排气压力, 液管温度)
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：饱和温度(排气压力) - 液管温度（3x9）
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            sc_raw = sat_temp_d - state.sensor_data[4]
            state.subcooling = int(sc_raw * 100 +
                (0.5 if sc_raw >= 0 else -0.5)) / 100
        except Exception as e:
            print("[Sensor] SC 计算异常，过冷度置 999:", e)
            state.subcooling = 999

        # 整机电流：采样失败只影响本通道（P0-1 修复：显式传 bits）
        try:
            adc_val = board.adc_current.read()
            state.sensor_data[9] = read_current(adc_val, bits=state.adc_bits["PA4_I"])
        except Exception as e:
            print("[Sensor] 电流采样异常，单通道置 999:", e)
            state.sensor_data[9] = 999

        # Step5a: 水流开关 DI（软件占位，1=正常，0=断开）
        try:
            state.state_data[state.ST_FLOW_SWITCH] = 1 if board.di_flow.value() else 0
        except Exception as e:
            print("[Sensor] 流量 DI 读取异常:", e)
            state.state_data[state.ST_FLOW_SWITCH] = 1

    finally:
        state.update_all_sensor_faults()
        validate()

    return state.sensor_data  # suction, discharge, inlet, outlet, liquid, suction_psi, discharge_psi, suction_sh, e_value, current


async def reads_async():
    """
    异步版本（Step4：去阻塞），采集一次全部传感器，写入 sensor_data[]；
    无论是否异常都保证调用 update_all_sensor_faults() + validate()，
    确保 sensor_quality / sensor_fault_reg 不会停留在旧状态。
    """
    try:
        rts = _get_rts()
        if rts is not None:
            try:
                tem_data = await rts.config_pt100_chs_async()
            except Exception as e:
                print("[Sensor] AD7124 读取异常，温度通道置 999:", e)
                tem_data = [999] * 5
        else:
            tem_data = [999] * 5

        for i in range(5):
            state.sensor_data[i] = tem_data[i]

        state.sensor_data[5] = psi_trans(board.adc_pc2c.value())
        state.sensor_data[6] = psi_trans(board.adc_pc3c.value())

        # 过热度 SH：查表失败只影响本通道
        try:
            sat_temp = read_sat_temp(state.sensor_data[5])
            sh_raw = state.sensor_data[0] - sat_temp
            state.sensor_data[7] = int(sh_raw * 100 +
                (0.5 if sh_raw >= 0 else -0.5)) / 100
        except Exception as e:
            print("[Sensor] SH 计算异常，单通道置 999:", e)
            state.sensor_data[7] = 999

        # 焓值 E（吸气焓，3x10）：查表失败只影响本通道
        try:
            state.sensor_data[8] = read_E(
                state.sensor_data[5], state.sensor_data[0])
        except Exception as e:
            print("[Sensor] E 查表异常，单通道置 999:", e)
            state.sensor_data[8] = 999

        # 液管焓值（出口焓，3x11）：read_E(排气压力, 液管温度)
        try:
            state.liquid_enthalpy = read_E(
                state.sensor_data[6], state.sensor_data[4])
        except Exception as e:
            print("[Sensor] 液管焓查表异常，置 999:", e)
            state.liquid_enthalpy = 999

        # 过冷度 SC：饱和温度(排气压力) - 液管温度（3x9）
        try:
            sat_temp_d = read_sat_temp(state.sensor_data[6])
            sc_raw = sat_temp_d - state.sensor_data[4]
            state.subcooling = int(sc_raw * 100 +
                (0.5 if sc_raw >= 0 else -0.5)) / 100
        except Exception as e:
            print("[Sensor] SC 计算异常，过冷度置 999:", e)
            state.subcooling = 999

        # 整机电流：采样失败只影响本通道（P0-1 修复：显式传 bits）
        try:
            adc_val = board.adc_current.read()
            state.sensor_data[9] = read_current(adc_val, bits=state.adc_bits["PA4_I"])
        except Exception as e:
            print("[Sensor] 电流采样异常，单通道置 999:", e)
            state.sensor_data[9] = 999

        # Step5a: 水流开关 DI（软件占位，1=正常，0=断开）
        try:
            state.state_data[state.ST_FLOW_SWITCH] = 1 if board.di_flow.value() else 0
        except Exception as e:
            print("[Sensor] 流量 DI 读取异常:", e)
            state.state_data[state.ST_FLOW_SWITCH] = 1

    finally:
        state.update_all_sensor_faults()
        validate()

    return state.sensor_data  # suction, discharge, inlet, outlet, liquid, suction_psi, discharge_psi, suction_sh, e_value, current

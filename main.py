# main.py -- put your code here!
import uasyncio as asyncio
from pyb import Pin
import time
import json
from HAL.PID_Plus import IncrementalController
# from EXV.EXV import step,async_run,get_pwm_status
import EXV.Valve_CRTL as exv_ctrl
from HAL.ADC_sample import SmoothedADC
from Display_Touch.Desplay import desplay
from Board import board
import Board.state as state
from Sensor.read_sensors import reads


# ------------------------
# 全局变量区
# ------------------------

import Board.state as state

# ------------------------
# Model A：Ctrl compressor speed
# ------------------------
async def Compressor():
    while True:
        if state.state_data[state.ST_SYSTEM_ENABLE]:
            # TODO: 压缩机驱动方案未定，这里先占位
            # 可用参数：state.compressor_control_params（Kp/Ki/Kd/setpoint等）
            # 闭环目标量待定（吸气压力/排气压力/制冷量需求），驱动方案确定后再接入
            pass
        else:
            # 系统未启动/被停止，确保压缩机保持停机状态
            pass
        await asyncio.sleep(0.5)

# ------------------------
# Model B：EXV open
# ------------------------
async def Exv():
    while True:
        await exv_ctrl.run()

        await asyncio.sleep_ms(1000)
    

# ------------------------
# Model C：desplay
# ------------------------
async def Display():
    while True:
        # print("[C] Display -> speed:", global_state['motor_speed'],
        #       "valve:", global_state['valve_pos'],
        #       "sensor:", global_state['sensor_value'])
        desplay()
        await asyncio.sleep(1)

# ------------------------
# Model D：read sensor and check alarm
# ------------------------
async def Sensor():
    elapsed = 0
    while True:
        try:
            board.adc_pc2.sample()
            board.adc_pc3.sample()
            elapsed += 12   #sample_interval_ms

            if elapsed >= 200:
                elapsed = 0
            
            reads()
        except Exception as e:
            print("[Sensor] 读取异常:", e)
        await asyncio.sleep_ms(200)


# ------------------------
# file log
# ------------------------
# async def Log():
#     while True:
#         try:
#             with open("data_log.txt", "a") as f:
#                 f.write(f"{time.time()},{global_state['sensor_value']},{global_state['motor_speed']},{global_state['valve_pos']}\n")
#         except:
#             print("[Log] File write error")

#         #state.save_control_params('config.json')   # 换掉不存在的 exv_ctrl.save_parameters
#         await asyncio.sleep(5000)

# ------------------------
# main
# ------------------------
async def main():


    # ── 初始化PID控制器 ───────────────────────────────────────
    ctrl_exv = IncrementalController(
        Kp              = state.control_params['Kp'],
        Ki              = state.control_params['Ki'],
        Kd              = state.control_params['Kd'],
        dt              = 1.0,
        setpoint        = state.control_params['setpoint'],
        deadband        = state.control_params['deadband'],
        max_delta       = state.control_params['max_delta'],
        tau             = state.control_params['tau'],
        error_threshold = state.control_params['error_threshold'],
        aggr_mode       = state.control_params['aggr_mode'],
        T_goal          = state.control_params['T_goal'],
    )
    
    # ── 注入PID控制器到EXV调度模块 ───────────────────────────
    exv_ctrl.set_controller(ctrl_exv)


    await asyncio.gather(
        Compressor(),
        Exv(),
        Display(),
        Sensor(),
        # Log()
    )

asyncio.run(main())


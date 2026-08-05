# main.py -- put your code here!
import uasyncio as asyncio
from pyb import Pin
import time
import json
from HAL.PID_Plus import IncrementalController
from EXV.EXV import step,async_run,get_pwm_status
from HAL.ADC_sample import SmoothedADC
from Display_Touch.Desplay import desplay
import Board.state as state
from Sensor.read_sensors import reads

# ------------------------
# 全局变量区
# ------------------------
global_state = {
    'motor_speed': 0,
    'valve_pos': 0,
    'sensor_value': 0,
    'sensor_fault': False,
}

# ------------------------
# Model A：Ctrl compressor speed
# ------------------------
async def Compressor():
    while True:
        

        await asyncio.sleep(0.5)  # 每0.5秒执行一次

# ------------------------
# Model B：EXV open
# ------------------------
async def Exv():
    global Error_point

    # ── 上电归零 ─────────────────────────────────────────────
    ok = await exv.homing()
    if not ok:
        print("[EXV] 归零失败")
        Error_point[0] = 1
        return

    # ── 控制循环 ─────────────────────────────────────────────
    while True:

        # 读取当前过热度（sensor_data[7]）
        sh = sensor_data[7]

        # 传感器故障判断（999 = PT100断路）
        if sh == 999:
            print("[EXV] 传感器故障，EXV保持当前开度")
            Error_point[1] = 1
            await asyncio.sleep_ms(1000)
            continue

        # PID计算
        delta = exv_ctrl.update(sh)

        # 开度控制
        if delta != 0:
            ok = await exv.move_delta_pct(delta)
            if not ok:
                print("[EXV] 驱动器故障")
                Error_point[0] = 1

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
            adc_pc2.sample()
            adc_pc3.sample()
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
async def Log():
    while True:
        try:
            with open("data_log.txt", "a") as f:
                f.write(f"{time.time()},{global_state['sensor_value']},{global_state['motor_speed']},{global_state['valve_pos']}\n")
        except:
            print("[Log] File write error")
        
        exv_ctrl.save_parameters('config.json')
        await asyncio.sleep(5)  # 每5秒写一次文件

# ------------------------
# main
# ------------------------
async def main():
    with open('config.json', 'r') as f:
        config = json.load(f)

    PID_data = list(config.values())
    # print(config['Kp'])
    # all_step = config['all_step']
    
    
    exv_ctrl = IncrementalController(Kp=0.6, Ki=0.12, Kd=3.0, dt=1.0, setpoint=5.0, deadband=0.1,
                                 max_delta=8.0, tau=3.0,
                                 error_threshold=2, aggr_mode="togoal", T_goal=5.0)


    await asyncio.gather(
        Compressor(),
        Exv(),
        Display(),
        Sensor(),
        Log()
    )

asyncio.run(main())


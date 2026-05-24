# main.py -- put your code here!
import uasyncio as asyncio
from pyb import Pin
import time
import json
from HAL.PID_Plus import IncrementalController
from EXV.EXV import step,async_run,get_pwm_status
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
sensor_data = []
PID_data = [] #Kp, Ki, Kd, setpiont, error_threshold, deadband, max_delta,  aggr_mode,  exv_frreq,  all_step
Error_point=[0,0,0,0,0,0,0,0]


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
    # while True:
    #     global_state['valve_pos'] = (global_state['motor_speed'] * 2) % 100
    #     print("[B] valve pos:", global_state['valve_pos'])
    #     await asyncio.sleep(1)  # 每1秒执行一次
    while True:
        async_run( , ,dir,ena,"PB7",4,2)
        



    

# ------------------------
# Model C：desplay
# ------------------------
async def Display():
    while True:
        print("[C] Display -> speed:", global_state['motor_speed'],
              "valve:", global_state['valve_pos'],
              "sensor:", global_state['sensor_value'])
        await asyncio.sleep(1)

# ------------------------
# Model D：read sensor and check alarm
# ------------------------
async def Sensor_Alarm():
    # while True:
    #     try:
    #         val=[]
    #         val = read_sensor()
    #         global_state['sensor_value'] = val
    #         if val < 0 or val > 1000:
    #             global_state['sensor_fault'] = True
    #             print("[D] Sensor fault detected!")
    #         else:
    #             global_state['sensor_fault'] = False
    #     except Exception as e:
    #         global_state['sensor_fault'] = True
    #         print("[D] Sensor error:", e)

    #     await asyncio.sleep(0.2)  # 每200ms采样一次
    while True:
        data = reads()
        sensor_data = data[:6]

        await asyncio.sleep(0.1)  # 每100ms采样一次




# ------------------------
# file log
# ------------------------
async def log_task():
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
        Sensor_Alarm(),
        log_task()
    )

# def read_sensor():
#     # 模拟采样函数
#     return time.ticks_ms() % 1000

asyncio.run(main())

 
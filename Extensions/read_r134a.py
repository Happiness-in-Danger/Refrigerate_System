import struct

RECORD_SIZE = 56  # 12个float = 56字节
P_START = 0.05
P_STEP = 0.01
BIN_FILE = "pt_table.bin"

def read_record(bin_file, pressure)-> tuple:
    index = int(round((pressure - P_START) / P_STEP))
    if index < 0:
        raise -999
    offset = index * RECORD_SIZE
    with open(bin_file, "rb") as f:
        f.seek(offset)
        raw = f.read(RECORD_SIZE)
    if len(raw) < RECORD_SIZE:
        raise 9999
    return struct.unpack("<14f", raw)

def _poly5(coeffs, dT):
    """coeffs = (a5, a4, a3, a2, a1, a0)"""
    a5, a4, a3, a2, a1, a0 = coeffs
    return a5*dT**5 + a4*dT**4 + a3*dT**3 + a2*dT**2 + a1*dT + a0

def read_sat_temp(pressure) -> float:
    data=read_record("Extensions/pt_table.bin", pressure)
    sat_temp=data[1] - 273.15
    return sat_temp

def read_E(psi,temp)-> float:
    data = read_record("Extensions/pt_table.bin", psi)
    sat_temp = read_sat_temp(psi)
    dT = temp - sat_temp
    if dT > 0:
        E = _poly5(data[8:14], dT)
    elif dT < 0:
        E = _poly5(data[2:8], dT)
    else:
        E = data[7]
    
    return int(E * 100) / 100

# 示例：查找 1.23 bar 对应的数据
# record = read_record("Extensions/pt_table.bin", 1.22)
# print(record[1])

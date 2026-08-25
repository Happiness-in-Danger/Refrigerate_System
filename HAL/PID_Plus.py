import math
import json

# ============================================================================
# 相较于 GitHub 原版 HAL/PID_Plus.py 的优化点汇总
# （原版地址：Happiness-in-Danger/Refrigerate_System @ HAL/PID_Plus.py）
# ----------------------------------------------------------------------------
# 【Bug 修复类】
#   1. error_threshold 默认值算错：
#      原版：error_threshold if ... else max(40*deadband, 0.0)
#      构造函数自己的注释写的却是 "None 时取 2*deadband"，40 明显是笔误，
#      会导致默认阈值比预期大 20 倍，"大误差快速调节"几乎永远触发不了。
#      -> 已改回 2*deadband，和注释、和实际传入的 error_threshold=2 语义一致。
#
#   2. 除零风险：
#      原版 update() 里多处用 self.dt、self.T_goal 做除数
#      （raw_dx、raw_ddx、r_per_sec），但构造函数完全没检查这两个值是否为 0。
#      -> __init__ 里加了 dt<=0 -> 1e-6、T_goal==0 -> 1e-6 的兜底。
#
#   3. 死区内输出被整体清零（原版里最隐蔽的一个逻辑 bug）：
#      原版：
#        delta_unsat = Kp_eff*eff_error + Ki_eff*self.integral + Kd_eff*deriv_term
#                      if eff_error != 0.0 else 0.0
#      只要落进死区，无论积分项累计了多少，整个输出直接清零：
#        - 积分项本来是用来消除稳态余差的，一进死区就被忽略，阀门可能
#          长期停在有偏差的位置上，永远靠积分修不回来；
#        - 从死区外进入死区的瞬间，输出会发生阶跃式突变，容易引起抖动。
#      -> 去掉这个整体清零判断；P 项在 eff_error==0 时本来就自然为 0，
#         不需要额外处理。
#
#   4. 符号函数在边界值上的偏差：
#      原版到处写 `1 if x > 0 else -1`，x 恰好等于 0 时会被误判成"负号"，
#      导致 Kp_eff/Kd_eff/Ki_eff 在该拍被莫名调小。
#      -> 抽成统一的 _sign()，x==0 时返回 0（不做方向性修正）。
#
#   5. 死代码 / 类型不一致（不影响运行，顺手清理）：
#      T_goal 的默认参数已经是 5.0，原版 "T_goal if ... else 10.0" 的 else
#      分支永远走不到；prev_dx_filtered 初始化用的是 int 0 而不是 0.0。
#
# 【结构性优化，非单纯 bug】
#   6. Kp 的自适应方向：原版只看 dx_filtered 的符号，没有结合 error 方向；
#      -> 改成 error * dx_filtered 的符号：远离目标（同号）就增强 Kp，
#         靠近目标（异号）就减弱 Kp，方向判断更贴近实际控制意图。
#
#   7. Kd 的自适应方向：原版只看 ddx_filtered 的符号，没有结合误差方向；
#      -> 按 Kp 的思路推广成 error * ddx_filtered 的符号。这一条推广还没
#         经过实测验证，如果发现阻尼方向不对，重点检查这里。
#
#   8. Ki 的自适应方向：原版用 dx_filtered 的符号，和"长期误差"的语义不太
#      贴合；-> 统一成 error * dx_filtered，和 Kp 收紧-放松的节奏保持一致。
#
#   9. Kp/Ki/Kd 从"每拍现算、用完即弃"变成"在线整定并持久化"：
#      原版的 Kp_eff/Ki_eff/Kd_eff 只是当拍的临时乘子，从不会反过来影响
#      self.Kp/Ki/Kd，下次调用还是从原始固定值重新算——本质上不是"整定"，
#      只是"每拍临时打折/加成"。
#      -> 新增 adapt_parameters()：用一个很小的学习率（gain_learn_rate）把
#         self.Kp/Ki/Kd 缓慢拉向 *_eff，让参数真正随运行过程收敛；
#         同时用 gain_bounds_ratio 限制漂移范围（相对初始值的倍数），
#         防止自适应发散失控。
#
#   10. 新增 save_parameters() / load_parameters()：
#       把在线收敛出来的 Kp/Ki/Kd 存成 json，下次启动直接加载，
#       不用每次重新整定——原版完全没有持久化机制。
#
#   11. 快速调节（aggressive）模式下不再调用 adapt_parameters()：
#       大误差时系统在做非线性大幅动作，不代表正常工况的动态特性，
#       不应该被"学习"进 Kp/Ki/Kd 里，否则会污染整定结果。
# ============================================================================


class IncrementalController:
    """
    通用的、基于被控量变化趋势进行在线参数调整的增量控制器。

    设计意图：
      - 控制器本身不绑定具体被控对象，只关心 meas / setpoint -> delta。
        典型用法：
            过热度 SH        -> 膨胀阀开度增量
            实际冷量         -> 压缩机频率增量
            压力 / 温度      -> 风机 / 水泵频率增量
      - 输出的是增量 delta，而不是绝对控制量，由调用方自己做
        u_{k+1} = u_k + delta 并做上下限裁剪（对应 demo() 里的 valve）。
      - 误差小的时候，主要靠被控量变化率 dx/dt（以及 d2x/dt2）做微调；
        误差大的时候（|error| > error_threshold），直接按误差本身做
        快速调节（aggressive 模式），不再等变化率慢慢反映。
      - Kp/Ki/Kd 不是写死的：每一拍都会算出 Kp_eff/Ki_eff/Kd_eff，
        它们既用于当前这一拍的输出，也是下一轮 self.Kp/Ki/Kd 的候选值，
        通过 adapt_parameters() 缓慢收敛，最终可以用 save_parameters()
        存盘、下次用 load_parameters() 直接加载，而不用每次从头整定。
    """

    def __init__(self,
                 Kp=0.6, Ki=0.12, Kd=3.0,
                 dt=1.0,
                 setpoint=5.0,
                 deadband=0.2,
                 max_delta=5.0,
                 tau=3.0,  # EMA
                 adapt_alpha=0.05,  # 单拍内 Kp_eff/Ki_eff/Kd_eff 的临时调整幅度
                 gain_learn_rate=0.02,  # Kp/Ki/Kd 本身向 *_eff 收敛的速度（在线整定的"学习率"）
                 gain_bounds_ratio=(0.2, 5.0),  # Kp/Ki/Kd 允许漂移的范围：[ratio_min*初始值, ratio_max*初始值]
                 # --- new params for aggressive behavior ---
                 error_threshold=2,  # if it's None it's max(2*deadband, user set)
                 aggr_mode="togoal",  # "off", "proportional", "togoal"
                 K_aggr=None,  # use in proportional (if it's None it's 3*Kp)
                 T_goal=5.0  # use in "togoal"
                 ):
        # ==== PID 参数：会在运行中被 adapt_parameters() 缓慢调整，可保存/加载 ====
        self.Kp = Kp; self.Ki = Ki; self.Kd = Kd
        self.dt = dt; self.setpoint = setpoint; self.deadband = deadband
        self.max_delta = max_delta; self.tau = max(1e-6, tau)
        self.adapt_alpha = adapt_alpha

        # dt<=0 会导致后面所有除以 dt 的地方直接崩溃，做一次防御
        if self.dt <= 0:
            self.dt = 1e-6

        # 记录初始增益，作为在线整定时的漂移边界基准（防止自适应把 Kp/Ki/Kd
        # 越调越离谱，最终失控）。ratio_min/ratio_max 是相对初始值的倍数。
        self._Kp0, self._Ki0, self._Kd0 = Kp, Ki, Kd
        self.gain_learn_rate = gain_learn_rate
        self._ratio_min, self._ratio_max = gain_bounds_ratio

        # ==== 控制状态 ====
        self.integral = 0.0
        self.prev_meas = None
        self.dx_filtered = 0.0
        self.prev_dx_filtered = 0.0
        self.ddx_filtered = 0.0

        # ==== 大误差快速调节相关参数 ====
        # 注释写的是 "None 时取 2*deadband"：大误差时直接按 error 本身快速调节，
        # 阈值应该和死区在同一量级，而不是死区的几十倍，否则快速调节
        # 几乎永远不会被触发。
        self.error_threshold = error_threshold if error_threshold is not None else max(2.0 * deadband, 0.0)
        self.aggr_mode = aggr_mode
        self.K_aggr = K_aggr if K_aggr is not None else (3.0 * self.Kp)  # default 3x Kp

        if T_goal is None:
            self.T_goal = 10.0
        elif T_goal <= 0:
            self.T_goal = 1e-6
        else:
            self.T_goal = T_goal

    @staticmethod
    def _sign(x):
        # x 恰好为 0 时既不算"正在恶化"也不算"正在好转"，返回 0，
        # 不对增益做任何方向性的临时调整。
        if x > 0:
            return 1
        elif x < 0:
            return -1
        return 0

    def _clamp_gain(self, value, base, ratio_min, ratio_max):
        lo = base * ratio_min
        hi = base * ratio_max
        if lo > hi:
            lo, hi = hi, lo
        return min(max(value, lo), hi)

    def adapt_parameters(self, Kp_eff, Ki_eff, Kd_eff):
        """
        Kp_eff/Ki_eff/Kd_eff 不只是当拍用一次就扔掉的临时值，而是"下一轮 PID
        参数的候选值"。这里用一个很小的学习率把
        self.Kp/Ki/Kd 慢慢往候选值方向拉一点，而不是直接整个替换掉——
        直接替换会让增益随单次噪声剧烈跳变，容易震荡；缓慢收敛才像"整定"。
        同时用 gain_bounds_ratio 限制漂移范围，避免自适应发散失控。
        """
        self.Kp += self.gain_learn_rate * (Kp_eff - self.Kp)
        self.Ki += self.gain_learn_rate * (Ki_eff - self.Ki)
        self.Kd += self.gain_learn_rate * (Kd_eff - self.Kd)

        self.Kp = self._clamp_gain(self.Kp, self._Kp0, self._ratio_min, self._ratio_max)
        self.Ki = self._clamp_gain(self.Ki, self._Ki0, self._ratio_min, self._ratio_max)
        self.Kd = self._clamp_gain(self.Kd, self._Kd0, self._ratio_min, self._ratio_max)

    def save_parameters(self, path):
        """把当前整定好的 Kp/Ki/Kd（以及相关状态量）存盘，对应第 11/12 节。"""
        data = {
            "Kp": self.Kp, "Ki": self.Ki, "Kd": self.Kd,
            "setpoint": self.setpoint, "deadband": self.deadband,
            "error_threshold": self.error_threshold,
            "K_aggr": self.K_aggr, "T_goal": self.T_goal,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_parameters(self, path):
        """加载上次保存的 PID 参数，避免每次启动都从头整定。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.Kp = data.get("Kp", self.Kp)
        self.Ki = data.get("Ki", self.Ki)
        self.Kd = data.get("Kd", self.Kd)
        self.setpoint = data.get("setpoint", self.setpoint)
        self.deadband = data.get("deadband", self.deadband)
        self.error_threshold = data.get("error_threshold", self.error_threshold)
        self.K_aggr = data.get("K_aggr", self.K_aggr)
        self.T_goal = max(1e-6, data.get("T_goal", self.T_goal))
        # 以加载到的增益作为新的漂移基准，避免下次整定时又被拉回旧的初始值
        self._Kp0, self._Ki0, self._Kd0 = self.Kp, self.Ki, self.Kd

    def update(self, meas, setpoint=None, measured_aux=None, force_disable_derivative=False):
        if setpoint is None:
            setpoint = self.setpoint
        else:
            self.setpoint = setpoint

        # 第一拍没有历史数据，不能算变化率，先记录基准
        if self.prev_meas is None:
            self.prev_meas = meas
            self.dx_filtered = 0.0
            return 0.0

        # 死区
        error = meas - setpoint
        eff_error = 0.0 if abs(error) <= self.deadband else error

        # 大误差时不再等变化率慢慢反映，直接按误差本身快速调节
        use_aggressive = (abs(error) > self.error_threshold) and (self.aggr_mode != "off")

        if use_aggressive:
            self.integral *= 0.5
            if self.aggr_mode == "proportional":
                delta_unsat = self.K_aggr * eff_error
            elif self.aggr_mode == "togoal":
                conv_factor = 1.0
                r_per_sec = eff_error / self.T_goal
                delta_unsat = conv_factor * r_per_sec * self.dt * 1.0
            else:
                delta_unsat = self.K_aggr * eff_error

            if delta_unsat > self.max_delta:
                delta = self.max_delta
            elif delta_unsat < -self.max_delta:
                delta = -self.max_delta
            else:
                delta = delta_unsat

            self.prev_meas = meas
            # 快速调节模式下系统处于非线性大幅动作阶段，不代表正常工况下的
            # 动态特性，所以不在这里调用 adapt_parameters()，避免把这段时间
            # 的行为错误地"学习"进 Kp/Ki/Kd 里。
            return delta

        # --- 正常（小误差）模式：靠变化率做微调 ---
        # 1) 一阶变化率 dx/dt
        raw_dx = (meas - self.prev_meas) / self.dt
        beta = (self.tau / (self.tau + self.dt))
        self.dx_filtered = beta * self.dx_filtered + (1.0 - beta) * raw_dx

        # 2) 二阶变化率 d2x/dt2
        raw_ddx = (self.dx_filtered - self.prev_dx_filtered) / self.dt
        beta2 = (1.5 * self.tau / (1.5 * self.tau + self.dt))
        self.ddx_filtered = beta2 * self.ddx_filtered + (1.0 - beta2) * raw_ddx
        self.prev_dx_filtered = self.dx_filtered

        # 3) 积分项累加
        self.integral += eff_error * self.dt

        # 4) 自适应增益
        # Kp：判断依据是 error * dx_filtered 的符号，而不是单独看
        #     dx_filtered 的符号——
        #     error*dx > 0 说明被控量正在远离目标，需要加强 Kp；
        #     error*dx < 0 说明正在靠近目标，适当减弱 Kp。
        alpha_sign_p = self._sign(error * self.dx_filtered)
        Kp_eff = self.Kp * (1.0 + alpha_sign_p * self.adapt_alpha * abs(error))

        # Kd：只看 ddx_filtered 的符号不够，还应该结合误差方向，这里按
        #     和 Kp 相同的原则做一次推广：用 error * ddx_filtered 的符号，
        #     即"二阶变化是不是在让误差变得更糟"。这块逻辑还没有经过
        #     实测验证，如果发现阻尼方向不对，重点检查这一行。
        alpha_sign_d = self._sign(error * self.ddx_filtered)
        Kd_eff = self.Kd * (1.0 + alpha_sign_d * self.adapt_alpha * abs(self.dx_filtered))

        # Ki：关心的是"长期存在的偏差"，所以只在误差明显超出死区
        #     （> 0.5*deadband）时才调整，方向沿用和 Kp 一致的
        #     "远离目标就增强"的判断，让 Ki 和 Kp 的收紧/放松保持同步。
        if abs(error) > 0.5 * self.deadband:
            alpha_sign_i = self._sign(error * self.dx_filtered)
            Ki_eff = self.Ki * (1.0 + alpha_sign_i * self.adapt_alpha)
        else:
            Ki_eff = self.Ki

        # 5) 是否使用微分项
        use_derivative = not force_disable_derivative
        if measured_aux is not None:
            if measured_aux < 1e-4:
                use_derivative = False
        deriv_term = self.dx_filtered if use_derivative else 0.0

        # 死区内 eff_error 已经是 0，P 项自然为 0，不需要再额外把整个
        # 输出强制清零——那样会把积分项/微分项的贡献一起吞掉，并且在
        # 刚进入死区的瞬间造成输出突变。
        delta_unsat = Kp_eff * eff_error + Ki_eff * self.integral + Kd_eff * deriv_term

        if delta_unsat > self.max_delta:
            delta = self.max_delta
            self.integral -= eff_error * self.dt  # 抗积分饱和：撤销这一拍的积分增量
        elif delta_unsat < -self.max_delta:
            delta = -self.max_delta
            self.integral -= eff_error * self.dt
        else:
            delta = delta_unsat

        self.prev_meas = meas

        # 6) 把这一拍算出来的 Kp_eff/Ki_eff/Kd_eff 作为候选值，缓慢并入
        #    self.Kp/Ki/Kd，让参数逐渐"整定收敛"，而不是每次都从固定
        #    初始值重新算一遍。
        self.adapt_parameters(Kp_eff, Ki_eff, Kd_eff)

        return delta


# ------------------ test ------------------
def demo():
    # 控制过热度 SH
    ctrl = IncrementalController(Kp=0.6, Ki=0.12, Kd=3.0, dt=1.0, setpoint=5.0, deadband=0.1,
                                  max_delta=8.0, tau=3.0,
                                  error_threshold=2, aggr_mode="togoal", T_goal=5.0)
    valve = 40.0
    SHs = [4.8, 5.2, 6.2, 7.5, 8.0, 7.0, 6.0, 5.4, 5.0, 4.99, 4.8]
    for sh in SHs:
        d = ctrl.update(sh)
        valve = max(0.0, min(100.0, valve + d))
        print(f"SH={sh:.2f} e={sh-ctrl.setpoint:+.2f} -> delt={d:+.2f}% => valve={valve:.2f}% "
              f"| Kp={ctrl.Kp:.4f} Ki={ctrl.Ki:.4f} Kd={ctrl.Kd:.4f}")

    # 演示保存 / 加载：把跑完这一轮之后收敛出来的 Kp/Ki/Kd 存盘，
    # 下次可以直接 load_parameters() 恢复，不用从头整定。
    ctrl.save_parameters("pid_params.json")

    ctrl2 = IncrementalController()
    ctrl2.load_parameters("pid_params.json")
    print(f"\n加载后的参数: Kp={ctrl2.Kp:.4f} Ki={ctrl2.Ki:.4f} Kd={ctrl2.Kd:.4f}")


if __name__ == "__main__":
    demo()

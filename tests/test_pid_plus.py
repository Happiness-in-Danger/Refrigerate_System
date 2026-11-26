from HAL.PID_Plus import IncrementalController


def test_integral_contributes_to_original_controller_output():
    ctrl = IncrementalController(
        Kp=0.0, Ki=2.0, Kd=0.0, dt=0.5,
        setpoint=0.0, deadband=0.0, max_delta=100.0,
        aggr_mode="off",
    )

    # The first sample establishes the measurement baseline.
    assert ctrl.update(3.0) == 0.0
    assert ctrl.update(3.0) == 3.0
    assert ctrl.update(3.0) == 6.0


def test_original_anti_windup_reverses_the_current_integral_step():
    ctrl = IncrementalController(
        Kp=0.0, Ki=1.0, Kd=0.0, dt=1.0,
        setpoint=0.0, deadband=0.0, max_delta=0.5,
        aggr_mode="off",
    )

    ctrl.update(0.0)
    assert ctrl.update(1.0) == 0.5
    assert ctrl.integral == 0.0


def test_second_derivative_participates_in_original_gain_adaptation():
    ctrl = IncrementalController(
        Kp=0.0, Ki=0.0, Kd=1.0, dt=1.0,
        setpoint=0.0, deadband=0.0, max_delta=100.0,
        tau=0.0, adapt_alpha=0.1, gain_learn_rate=0.1,
        aggr_mode="off",
    )

    ctrl.update(0.0)
    ctrl.update(1.0)
    ctrl.update(3.0)

    assert ctrl.ddx_filtered > 0.0
    assert ctrl.Kd > 1.0

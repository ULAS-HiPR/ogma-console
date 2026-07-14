from ogma_app.boards import profile_for


def test_board_capabilities_match_current_support() -> None:
    croi = profile_for("croi")
    assert croi.can_build()
    assert croi.can_flash()
    assert croi.can_read_status()
    assert croi.can_evaluate_health()
    assert croi.can_read_flash_log()

    lamh = profile_for("lamh")
    assert lamh.can_read_status()
    assert lamh.can_command_servo()
    assert lamh.can_run_lamh_servo_test()
    assert [output.pca_channel for output in lamh.servo_outputs] == [0, 2, 4, 6]

    groundstation = profile_for("groundstation")
    assert not groundstation.can_build()
    assert groundstation.can_use_groundstation_usb()
    assert not groundstation.can_run_teachtaire_test()
    assert not groundstation.can_run_foinse_monitor()

    teachtaire = profile_for("teachtaire")
    assert teachtaire.can_run_teachtaire_test()
    assert not teachtaire.can_run_lamh_servo_test()

    pleasc = profile_for("pleasc")
    assert pleasc.can_build()
    assert pleasc.can_read_status()
    assert pleasc.can_evaluate_health()
    assert "stm32f072c8t6_rev1_pyro" in pleasc.env_names()

    foinse = profile_for("foinse")
    assert foinse.can_run_foinse_monitor()

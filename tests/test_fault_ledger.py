from ogma_app.fault_ledger import FaultLedger, board_fault_observations


def test_fault_ledger_latches_history_and_counts_recurrence(tmp_path) -> None:
    ledger = FaultLedger()
    fault = {"can_bus_off": 1}
    healthy = {"can_bus_off": 0}

    ledger.observe(board_fault_observations("foinse", fault), 1.0)
    ledger.observe(board_fault_observations("foinse", healthy), 2.0)
    ledger.observe(board_fault_observations("foinse", fault), 3.0)

    entry = ledger.entries["foinse.can_bus_off"]
    assert entry.active
    assert entry.occurrences == 2
    assert ledger.save(tmp_path / "faults.json").exists()


def test_croi_critical_faults_are_structured() -> None:
    observations = board_fault_observations(
        "croi",
        {
            "init_ok": 1,
            "logger_fault_latched": 1,
            "logger_fault": 5,
            "sensor_sample_valid": 1,
            "watchdog_init_ok": 1,
        },
    )

    logger = next(item for item in observations if item.key == "croi.logger")
    assert logger.active
    assert logger.severity == "critical"

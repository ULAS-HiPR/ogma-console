#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>

#include <tools/airbrake_logic.h>
#include <tools/flight_phase_logic.h>

namespace {

double argument(char** argv, int index) {
    return std::strtod(argv[index], nullptr);
}

}

int main(int argc, char** argv) {
    if (argc != 31) {
        std::cerr << "expected 30 replay configuration arguments\n";
        return 2;
    }

    FlightPhaseLogic phase_logic(
        PhaseDetectionConfig{
            static_cast<float>(argument(argv, 1)),
            static_cast<uint32_t>(argument(argv, 16)),
            static_cast<float>(argument(argv, 17)),
            static_cast<uint32_t>(argument(argv, 18)),
            static_cast<float>(argument(argv, 19)),
            static_cast<uint32_t>(argument(argv, 20)),
            static_cast<uint32_t>(argument(argv, 21)),
            static_cast<uint32_t>(argument(argv, 22)),
            static_cast<float>(argument(argv, 23)),
            static_cast<float>(argument(argv, 24)),
            static_cast<uint32_t>(argument(argv, 25)),
            static_cast<uint32_t>(argument(argv, 26)),
            static_cast<float>(argument(argv, 27)),
            static_cast<float>(argument(argv, 28)),
            static_cast<uint32_t>(argument(argv, 29)),
        },
        static_cast<float>(argument(argv, 2)),
        static_cast<uint32_t>(argument(argv, 3)),
        MainRecoveryFallback{
            argument(argv, 4) != 0.0,
            static_cast<uint32_t>(argument(argv, 5)),
            static_cast<float>(argument(argv, 6)),
            static_cast<float>(argument(argv, 7)),
            static_cast<float>(argument(argv, 8)),
            static_cast<uint16_t>(argument(argv, 9)),
        });
    AirbrakeLogic airbrake_logic(
        argument(argv, 10) != 0.0,
        static_cast<uint8_t>(argument(argv, 11)),
        static_cast<uint8_t>(argument(argv, 12)),
        static_cast<uint8_t>(argument(argv, 13)),
        static_cast<uint32_t>(argument(argv, 14)),
        static_cast<uint32_t>(argument(argv, 15)));

    State state = State::READY;
    uint32_t state_entry_ms = 0U;
    bool first_sample = true;
    uint32_t last_imu_ok_ms = 0U;
    uint32_t last_baro_ok_ms = 0U;
    std::string line;
    std::cout << "time_ms,state,transition,main_backup,airbrake_active,airbrake_angle_deg,"
                 "candidate_mask,confirmed_vote_mask,gate_mask,rejection_mask,transition_reason,"
                 "detector_mode,required_votes\n";
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        std::istringstream row(line);
        std::string field;
        double values[7]{};
        bool valid = true;
        for (double& value : values) {
            if (!std::getline(row, field, ',')) {
                valid = false;
                break;
            }
            value = std::strtod(field.c_str(), nullptr);
        }
        if (!valid) {
            std::cerr << "invalid replay row: " << line << '\n';
            return 3;
        }

        const uint32_t time_ms = static_cast<uint32_t>(values[0]);
        if (first_sample) {
            state_entry_ms = time_ms;
            last_imu_ok_ms = time_ms;
            last_baro_ok_ms = time_ms;
            first_sample = false;
        }
        const bool imu_valid = values[5] != 0.0;
        const bool baro_valid = values[6] != 0.0;
        if (imu_valid) last_imu_ok_ms = time_ms;
        if (baro_valid) last_baro_ok_ms = time_ms;
        const uint32_t sensor_fault_timeout_ms = static_cast<uint32_t>(argument(argv, 30));
        const bool imu_healthy = imu_valid || (time_ms - last_imu_ok_ms) <= sensor_fault_timeout_ms;
        const bool baro_healthy = baro_valid || (time_ms - last_baro_ok_ms) <= sensor_fault_timeout_ms;
        const State previous = state;
        state = phase_logic.update(
            state,
            PhaseSample{
                time_ms,
                static_cast<float>(values[1]),
                static_cast<float>(values[2]),
                static_cast<float>(values[3]),
                static_cast<float>(values[4]),
                imu_valid,
                baro_valid,
                imu_valid && baro_valid,
                imu_healthy,
                baro_healthy,
            },
            time_ms - state_entry_ms);
        const bool transitioned = state != previous;
        if (transitioned) {
            state_entry_ms = time_ms;
        }
        const AirbrakeCommand airbrake = airbrake_logic.update(state, time_ms);
        const PhaseDiagnostics& diagnostics = phase_logic.diagnostics();
        std::cout << time_ms << ','
                  << static_cast<int>(state) << ','
                  << (transitioned ? 1 : 0) << ','
                  << (phase_logic.main_backup_triggered() ? 1 : 0) << ','
                  << (airbrake.active ? 1 : 0) << ','
                  << static_cast<int>(airbrake.angle_deg) << ','
                  << diagnostics.candidate_mask << ','
                  << diagnostics.confirmed_vote_mask << ','
                  << diagnostics.gate_mask << ','
                  << diagnostics.rejection_mask << ','
                  << static_cast<int>(diagnostics.last_transition_reason) << ','
                  << static_cast<int>(diagnostics.detector_mode) << ','
                  << static_cast<int>(diagnostics.required_votes) << '\n';
    }
    return 0;
}

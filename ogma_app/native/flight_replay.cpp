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
    if (argc != 16) {
        std::cerr << "expected 15 replay configuration arguments\n";
        return 2;
    }

    FlightPhaseLogic phase_logic(
        static_cast<float>(argument(argv, 1)),
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
    std::string line;
    std::cout << "time_ms,state,transition,main_backup,airbrake_active,airbrake_angle_deg\n";
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        std::istringstream row(line);
        std::string field;
        double values[4]{};
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
            first_sample = false;
        }
        const State previous = state;
        state = phase_logic.update(
            state,
            static_cast<float>(values[1]),
            static_cast<float>(values[2]),
            static_cast<float>(values[3]),
            time_ms - state_entry_ms);
        const bool transitioned = state != previous;
        if (transitioned) {
            state_entry_ms = time_ms;
        }
        const AirbrakeCommand airbrake = airbrake_logic.update(state, time_ms);
        std::cout << time_ms << ','
                  << static_cast<int>(state) << ','
                  << (transitioned ? 1 : 0) << ','
                  << (phase_logic.main_backup_triggered() ? 1 : 0) << ','
                  << (airbrake.active ? 1 : 0) << ','
                  << static_cast<int>(airbrake.angle_deg) << '\n';
    }
    return 0;
}

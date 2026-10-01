// Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
#include <iostream>
#include <mutex>
#include <pqrs/karabiner/driverkit/virtual_hid_device_service.hpp>
#include <string>
#include <unistd.h>

int main() {
  if (geteuid() != 0) {
    std::cerr << "The virtual mouse client must run as root.\n";
    return 1;
  }

  pqrs::dispatcher::extra::initialize_shared_dispatcher();
  {
    std::mutex output_mutex;
    bool was_ready = false;
    const auto report = [&output_mutex](const std::string& message) {
      const std::lock_guard<std::mutex> lock(output_mutex);
      std::cout << message << '\n' << std::flush;
    };
    pqrs::karabiner::driverkit::virtual_hid_device_service::client client;

    client.connected.connect([&client] {
      client.async_virtual_hid_pointing_initialize();
    });
    client.virtual_hid_pointing_ready.connect([&was_ready, &report](bool ready) {
      if (ready && !was_ready) {
        was_ready = true;
        report("READY");
      } else if (!ready && was_ready) {
        report("ERROR virtual mouse disconnected");
      }
    });
    client.connect_failed.connect([&report](const auto& error) {
      report("ERROR cannot connect to the virtual HID daemon: " + error.message());
    });
    client.error_occurred.connect([&report](const auto& error) {
      report("ERROR virtual HID client: " + error.message());
    });
    client.closed.connect([&report] {
      report("ERROR virtual HID connection closed");
    });
    client.driver_version_mismatched.connect([&report](bool mismatched) {
      if (mismatched) {
        report("ERROR rebuild the prototype against the installed driver");
      }
    });
    client.async_start();

    std::string input;
    std::getline(std::cin, input);
    client.async_virtual_hid_pointing_terminate();
    client.async_stop();
  }
  pqrs::dispatcher::extra::terminate_shared_dispatcher();
  return 0;
}

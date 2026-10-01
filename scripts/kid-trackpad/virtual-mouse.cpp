// Copyright (C) 2026 Tyler Laprade. SPDX-License-Identifier: GPL-3.0-only
#include <chrono>
#include <condition_variable>
#include <exception>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <pqrs/karabiner/driverkit/virtual_hid_device_driver.hpp>
#include <pqrs/karabiner/driverkit/virtual_hid_device_service.hpp>
#include <string>
#include <unistd.h>

int main() try {
  constexpr auto release_timeout = std::chrono::seconds(5);
  if (geteuid() != 0) {
    std::cerr << "The virtual mouse client must run as root.\n";
    return 1;
  }

  pqrs::dispatcher::extra::initialize_shared_dispatcher();
  bool released = true;
  {
    std::mutex output_mutex;
    std::mutex state_mutex;
    std::condition_variable state_changed;
    bool pointing_ready = false;
    bool was_ready = false;
    const auto report = [&output_mutex](const std::string &message) {
      const std::scoped_lock lock(output_mutex);
      std::cout << message << '\n' << std::flush;
    };
    pqrs::karabiner::driverkit::virtual_hid_device_service::client client;

    client.connected.connect(
        [&client] { client.async_virtual_hid_pointing_initialize(false); });
    client.virtual_hid_pointing_ready.connect([&was_ready, &report,
                                               &state_mutex, &state_changed,
                                               &pointing_ready](bool ready) {
      {
        const std::scoped_lock lock(state_mutex);
        pointing_ready = ready;
      }
      state_changed.notify_all();
      if (ready && !was_ready) {
        was_ready = true;
        report("READY");
      } else if (!ready && was_ready) {
        report("ERROR virtual mouse disconnected");
      }
    });
    client.connect_failed.connect([&report](const auto &error) {
      report("ERROR cannot connect to the virtual HID daemon: " +
             error.message());
    });
    client.error_occurred.connect([&report](const auto &error) {
      report("ERROR virtual HID client: " + error.message());
    });
    client.closed.connect(
        [&report] { report("ERROR virtual HID connection closed"); });
    client.driver_version_mismatched.connect([&report](bool mismatched) {
      if (mismatched) {
        report("ERROR rebuild the prototype against the installed driver");
      }
    });
    client.async_start();

    std::cin.get();
    client.async_virtual_hid_pointing_terminate();
    {
      std::unique_lock<std::mutex> lock(state_mutex);
      released = state_changed.wait_for(
          lock, release_timeout, [&pointing_ready] { return !pointing_ready; });
    }
    client.async_stop();
  }
  pqrs::dispatcher::extra::terminate_shared_dispatcher();
  if (!released) {
    std::cerr << "The driver did not confirm releasing the virtual mouse.\n";
    return 1;
  }
  return 0;
} catch (const std::exception &error) {
  std::cerr << "Virtual mouse client failed: " << error.what() << '\n';
  return 1;
} catch (...) {
  std::cerr << "Virtual mouse client failed with an unknown exception.\n";
  return 1;
}

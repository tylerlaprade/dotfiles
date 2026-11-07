import AppKit
import Carbon
import Darwin
import Foundation

enum SchedulerError: Error {
    case invalidArguments
    case missingScript
    case automationPermission(OSStatus)
    case processFailure(Int32)
}

@main
enum ResumeScheduler {
    static func checkAutomation(requestPermission: Bool) throws {
        let target = NSAppleEventDescriptor(bundleIdentifier: "com.mitchellh.ghostty")
        let status = AEDeterminePermissionToAutomateTarget(
            target.aeDesc,
            typeWildCard,
            typeWildCard,
            requestPermission
        )
        guard status == noErr else {
            throw SchedulerError.automationPermission(status)
        }
    }

    static func runSchedule(_ identifier: String) throws {
        guard UUID(uuidString: identifier) != nil else {
            throw SchedulerError.invalidArguments
        }
        guard let script = Bundle.main.object(forInfoDictionaryKey: "ResumeSchedulerScript") as? String else {
            throw SchedulerError.missingScript
        }
        let python = "/opt/homebrew/bin/python3"
        var arguments = [python, script, "run", identifier].map { value in
            value.withCString { strdup($0) }
        }
        arguments.append(nil)
        var environment = ProcessInfo.processInfo.environment.map { key, value in
            "\(key)=\(value)".withCString { strdup($0) }
        }
        environment.append(nil)
        defer {
            arguments.forEach { free($0) }
            environment.forEach { free($0) }
        }
        var child: pid_t = 0
        // Foundation's Process creates a separate group, which escapes launchd's cancellation cleanup.
        let spawned = posix_spawn(&child, python, nil, nil, &arguments, &environment)
        guard spawned == 0 else {
            throw SchedulerError.processFailure(spawned)
        }
        var status: Int32 = 0
        while waitpid(child, &status, 0) == -1 {
            guard errno == EINTR else {
                throw SchedulerError.processFailure(errno)
            }
        }
        // Darwin's wait-status macros are not available in Swift.
        let signal = status & 0x7f
        exit(signal == 0 ? (status >> 8) & 0xff : 128 + signal)
    }

    static func dispatch(_ arguments: [String]) throws {
        switch arguments.first {
        case "run" where arguments.count == 2:
            try runSchedule(arguments[1])
        case "check" where arguments.count == 1:
            try checkAutomation(requestPermission: false)
        case "authorize" where arguments.count == 1:
            try checkAutomation(requestPermission: true)
        default:
            throw SchedulerError.invalidArguments
        }
    }

    static func main() {
        do {
            try dispatch(Array(CommandLine.arguments.dropFirst()))
        } catch {
            let message = "Resume Scheduler: \(error). Allow Resume Scheduler to control Ghostty in Privacy & Security > Automation.\n"
            FileHandle.standardError.write(Data(message.utf8))
            exit(1)
        }
    }
}

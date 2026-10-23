// Prints your upcoming timed meetings from the macOS Calendar (EventKit) as JSON.
//
//   calendar-events                  meetings in the next 24 h: [{uid,title,start,end,local,calendar}]
//   calendar-events --list-calendars calendar names only (to choose which ones to follow)
//
// All-day, cancelled and declined events are skipped. Exit code 2 means Calendar access was
// not granted (System Settings > Privacy & Security > Calendars).
// Build: see tools/build_calendar_helper.sh

import EventKit
import Foundation

func fail(_ message: String, code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

let store = EKEventStore()
let done = DispatchSemaphore(value: 0)
var granted = false
store.requestFullAccessToEvents { ok, _ in
    granted = ok
    done.signal()
}
done.wait()
guard granted else { fail("calendar access not granted", code: 2) }

let calendars = store.calendars(for: .event)
if CommandLine.arguments.contains("--list-calendars") {
    let names = calendars.map { ["name": $0.title, "account": $0.source.title] }
    let data = try! JSONSerialization.data(withJSONObject: names, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(0)
}

let now = Date()
let predicate = store.predicateForEvents(withStart: now.addingTimeInterval(-60),
                                         end: now.addingTimeInterval(24 * 3600), calendars: calendars)
let clock = DateFormatter()
clock.dateFormat = "HH:mm"

var events: [[String: Any]] = []
for event in store.events(matching: predicate) {
    if event.isAllDay || event.status == .canceled || event.startDate < now.addingTimeInterval(-60) { continue }
    if let me = event.attendees?.first(where: { $0.isCurrentUser }), me.participantStatus == .declined { continue }
    events.append([
        "uid": event.calendarItemExternalIdentifier ?? event.eventIdentifier ?? "",
        "title": event.title ?? "Meeting",
        "start": event.startDate.timeIntervalSince1970,
        "end": event.endDate.timeIntervalSince1970,
        "local": clock.string(from: event.startDate),
        "calendar": event.calendar.title,
    ])
}
events.sort { ($0["start"] as! Double) < ($1["start"] as! Double) }
let data = try! JSONSerialization.data(withJSONObject: events, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)

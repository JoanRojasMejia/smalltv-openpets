#!/bin/sh
# Builds tools/bin/calendar-events, embedding the Info.plist that macOS needs to ask for Calendar access.
set -eu
cd "$(dirname "$0")"
mkdir -p bin
swiftc -O CalendarEvents.swift -o bin/calendar-events \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker calendar-events-Info.plist
codesign --force --sign - --identifier com.joanrojas.smalltv-openpets.calendar-events bin/calendar-events
echo "built $(pwd)/bin/calendar-events"

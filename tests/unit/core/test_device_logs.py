# SPDX-License-Identifier: Apache-2.0
"""A real device's log, kept while it is driven by cable: its latest lines within a budget, read as a simulator's is."""

from __future__ import annotations

import threading

from sim_mirror.core.device_logs import DeviceLogBook, LogBuffer, Reader, process_of, wanted
from sim_mirror.testing.fakes import PHONE_UDID, ManualClock

NOTES = "Sep 29 00:47:18 Test-iPhone Notes(CoreData)[4321] <Notice>: saved draft"
FAILED = "Sep 29 00:47:19 Test-iPhone Notes[4321] <Error>: save failed"
FAULT = "Sep 29 00:47:20 Test-iPhone backboardd[74] <Fault>: display reset"
NOISE = "Sep 29 00:47:21 Test-iPhone apsd(libusrtcp.dylib)[146] <Notice>: tcp retransmit"


def test_a_line_names_its_process_and_is_an_apps_or_an_error() -> None:
    assert [process_of(line) for line in (NOTES, FAILED, FAULT, NOISE, "not syslog")] == [
        "Notes",
        "Notes",
        "backboardd",
        "apsd",
        None,
    ]
    assert wanted(NOTES, "com.example.Notes") and wanted(FAILED, "com.example.notes")
    assert not wanted(NOISE, "com.example.Notes") and wanted("x com.example.Notes y", "com.example.Notes")
    assert [wanted(line, None) for line in (NOTES, FAILED, FAULT, NOISE)] == [False, True, True, False]


def test_an_apps_line_is_found_by_its_executable_when_the_bundle_s_last_part_names_another() -> None:
    august = "Oct 27 10:00:00 Test-iPhone August(UIKitCore)[812] <Notice>: lock state changed"
    assert not wanted(august, "com.august.iossapp"), "the bundle's last part is not the process"
    assert wanted(august, "com.august.iossapp", "August") and wanted(august, "com.august.iossapp", "august")
    assert wanted("Oct 27 10:00:01 Test-iPhone iossapp[9] <Notice>: hi", "com.august.iossapp", "August")
    assert wanted("x com.august.iossapp y", "com.august.iossapp", "August"), "the bundle named in a line still is"
    assert not wanted(NOISE, "com.august.iossapp", "August")
    buffer = LogBuffer(1 << 20)
    buffer.add(august)
    buffer.add(NOISE)
    assert buffer.lines(60, "com.august.iossapp", "August") == [august]
    assert buffer.lines(60, "com.august.iossapp") == []
    assert buffer.lines(60, None, "August") == [], "with no app named, only errors and faults"


def test_the_buffer_keeps_the_latest_lines_within_its_budget_and_answers_by_time() -> None:
    clock = ManualClock(100.0)
    buffer = LogBuffer(len(NOTES) + len(FAILED), clock=clock)
    buffer.add(NOISE)
    clock.now += 10
    buffer.add(NOTES)
    buffer.add(FAILED)
    assert buffer.lines(60, "com.example.Notes") == [NOTES, FAILED], "the oldest went to make room"
    clock.now += 30
    buffer.add(FAULT)
    assert buffer.lines(20, None) == [FAULT] and buffer.lines(60, None) == [FAILED, FAULT]


class Stream:
    """A device's log relay: chunks that split lines anywhere, then the end -- or a failure."""

    def __init__(self, chunks: list[bytes], fail: bool = False) -> None:
        self.chunks = list(chunks)
        self.fail = fail
        self.closed = threading.Event()

    def recv(self, size: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        if self.fail:
            raise OSError("the cable was pulled")
        self.closed.wait(2)
        return b""

    def close(self) -> None:
        self.closed.set()


def test_a_reader_keeps_every_whole_line_however_the_stream_splits_them() -> None:
    raw = f"{NOTES}\n\x00{FAILED}\n\x00{NOISE}".encode() + b"\n\x00\n\xff\xfe\n"
    stream = Stream([raw[:30], raw[30:95], raw[95:]])
    reader = Reader(stream, LogBuffer(1 << 20)).start()
    reader.stop()
    assert stream.closed.is_set() and not reader.alive
    kept = reader.buffer.lines(60, None) + reader.buffer.lines(60, "com.example.Notes")
    assert FAILED in kept and NOTES in kept
    assert reader.buffer.lines(60, "\ufffd\ufffd") == ["\ufffd\ufffd"], "bytes that are not text are kept as such"
    failing = Reader(Stream([NOTES.encode() + b"\n"], fail=True), LogBuffer(1 << 20)).start()
    failing._thread.join(2)
    assert not failing.alive and failing.buffer.lines(60, "com.example.Notes") == [NOTES]


def test_a_reader_stopped_before_it_reads_reads_nothing() -> None:
    stream = Stream([NOTES.encode() + b"\n"])
    reader = Reader(stream, LogBuffer(1 << 20))
    reader._stopped.set()
    reader._run()
    assert reader.buffer.lines(60, "com.example.Notes") == [] and stream.chunks


def _read(book: DeviceLogBook, last: str) -> list[str] | None:
    """The device's errors and faults once its reader has read `last`."""
    for _ in range(200):
        kept = book.lines(PHONE_UDID, since_s=60, bundle_id=None)
        if kept and kept[-1] == last:
            return kept
        threading.Event().wait(0.01)
    return book.lines(PHONE_UDID, since_s=60, bundle_id=None)


def test_a_book_keeps_each_attached_devices_log_until_it_is_let_go() -> None:
    book = DeviceLogBook()
    assert book.lines(PHONE_UDID, since_s=60, bundle_id=None) is None
    first = Stream([f"{FAILED}\n".encode()])
    book.start(PHONE_UDID, first, max_bytes=1 << 20)
    assert _read(book, FAILED) == [FAILED]
    second = Stream([f"{FAULT}\n".encode()])
    book.start(PHONE_UDID, second, max_bytes=1 << 20)
    assert first.closed.is_set(), "a device's earlier log stops when a new one starts"
    assert _read(book, FAULT) == [FAILED, FAULT], "and what it read is kept"
    book.stop(PHONE_UDID)
    book.stop(PHONE_UDID)
    assert second.closed.is_set() and book.lines(PHONE_UDID, since_s=60, bundle_id=None) is None


def test_a_device_attached_again_after_its_cable_came_out_keeps_the_log_read_before() -> None:
    book = DeviceLogBook()
    book.start(PHONE_UDID, Stream([f"{FAILED}\n".encode()]), max_bytes=1 << 20)
    assert _read(book, FAILED) == [FAILED]
    book.stop(PHONE_UDID)
    assert book.lines(PHONE_UDID, since_s=60, bundle_id=None) is None, "nothing is answered while it is let go"
    book.start(PHONE_UDID, Stream([f"{FAULT}\n".encode()]), max_bytes=len(FAULT))
    assert _read(book, FAULT) == [FAULT], "a smaller budget lets the oldest lines go"
    book.start(PHONE_UDID, Stream([f"{FAILED}\n".encode()]), max_bytes=1 << 20)
    assert _read(book, FAILED) == [FAULT, FAILED]
    book.stop(PHONE_UDID)
    book.start("00008150-0099887766554433", Stream([]), max_bytes=1 << 20)
    assert book.lines("00008150-0099887766554433", since_s=60, bundle_id=None) == [], "each device keeps its own"
    book.stop("00008150-0099887766554433")

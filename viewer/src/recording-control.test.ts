// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeSocket } from '../test-support/fake-socket'
import { CLOCK, flush, live } from '../test-support/viewer-harness'
import { lucideSvg } from './icons'
import { createNote } from './note'
import { createLayers } from './popover'
import type { Recording, RecordingFinishing, RecordingList, RecordingState } from './protocol.generated'
import { createRecordingControl, stillRendering, type RecordingTransport } from './recording-control'
import type { StoppedRecording } from './transport'

const UNDER_WAY: RecordingState = { id: 'r1', since_ms: 0, by: 'person', max_ms: 300000 }
const PATH = '/r/r1.mp4'
/** What a stop answers of a recording that takes longer to render than the server waits. */
const RENDERING: StoppedRecording = {
  id: 'r1', device: 'Test iPhone', started_at: '2026-09-29T00:45:15Z', duration_ms: 600_000, files: [],
  notes: [`still being rendered; it will be kept at ${PATH}, and listed with the recordings once it is`],
  rendering: true, capture: null,
}
const KEPT: Recording = {
  id: 'r1', device: 'Test iPhone', started_at: '2026-09-29T00:45:15Z', duration_ms: 600_000, notes: [],
  files: [{ name: 'r1.mp4', path: PATH, format: 'mp4', bytes: 9_000_000, width: 900, height: 1952 }], capture: null,
}
const FINISHING: RecordingFinishing = {
  id: 'r1', device: 'Test iPhone', started_at: '2026-09-29T00:45:15Z', recorded_ms: 600_000, stopped_by_itself: false,
  paths: [PATH],
}
const OTHER: Recording = { ...KEPT, id: 'r0', files: [] }

function listed(recordings: Recording[], finishing: RecordingFinishing[] | null = []): RecordingList {
  // An older server sends no `finishing`.
  return { recordings, recording: null, ...(finishing ? { finishing } : {}) } as RecordingList
}

/** A control on its own, its transport's calls to read, and its Record button and note. */
function control(transport: Partial<RecordingTransport> = {}) {
  const calls = {
    startRecording: vi.fn(async () => UNDER_WAY),
    stopRecording: vi.fn(async (): Promise<StoppedRecording> => RENDERING),
    recordingFile: vi.fn(async () => new Blob(['movie'])),
    recordings: vi.fn(async () => listed([], [FINISHING])),
    ...transport,
  }
  const button = document.createElement('button')
  const noteEl = document.createElement('div')
  const made = createRecordingControl({
    button, clock: document.createElement('span'), noteEl, note: createNote(noteEl, lucideSvg), icon: lucideSvg,
    root: document.createElement('div'), sizeToggle: document.createElement('button'),
    sizePanel: document.createElement('div'), layers: createLayers(), transport: calls,
  })
  made.offer(true)
  return { calls, button, noteEl, made }
}

/** Start a recording and stop it, answering what the stop answers. */
async function recordAndStop(button: HTMLButtonElement) {
  button.click()
  await flush()
  button.click()
  await flush()
}

/** Let `ms` go by on the fake clock, a second at a time, each look answered before the next second. */
async function pass(ms: number) {
  for (let left = ms; left > 0; left -= 1000) {
    await vi.advanceTimersByTimeAsync(Math.min(left, 1000))
    await flush()
  }
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 1206, height: 2622, close: vi.fn() })))
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn() })) as never
  vi.useFakeTimers(CLOCK)
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('a recording still being rendered when it stopped', () => {
  it('is offered with its files once it is kept, looked for less often as it takes longer', async () => {
    const recordings = vi.fn<() => Promise<RecordingList>>()
      .mockRejectedValueOnce(new Error('the server is busy'))
      .mockResolvedValueOnce(listed([OTHER], null))
      .mockResolvedValueOnce(listed([OTHER], [FINISHING]))
      .mockResolvedValueOnce(listed([KEPT, OTHER], []))
    const { $ } = await live({
      transport: {
        startRecording: vi.fn(async () => UNDER_WAY), stopRecording: vi.fn(async () => RENDERING),
        recordingFile: vi.fn(async () => new Blob(['movie'])), recordings,
      },
    })
    await recordAndStop($<HTMLButtonElement>('[data-smv="record"]'))
    const note = $<HTMLElement>('[data-smv-note]')
    expect(note.textContent).toContain(`still being rendered; it will be kept at ${PATH}`)
    expect(note.querySelectorAll('[data-smv-save]')).toHaveLength(0)
    // Every few seconds, each wait longer than the last: a failed look, and an older server's list, look again.
    for (const [wait, looks] of [[1999, 0], [1, 1], [2999, 1], [1, 2], [4500, 3]] as const) {
      await pass(wait)
      expect(recordings).toHaveBeenCalledTimes(looks)
    }
    expect(note.textContent).toContain('still being rendered')
    await pass(6750)
    expect(recordings).toHaveBeenCalledTimes(4)
    expect(note.textContent).toContain('Recorded 10:00 of Test iPhone.')
    expect(note.textContent).not.toContain('still being rendered')
    expect([...note.querySelectorAll('[data-smv-save]')].map((save) => save.textContent))
      .toEqual(['Save MP4 · 900×1952 · 9.0 MB'])
    await pass(60 * 60_000)
    expect(recordings).toHaveBeenCalledTimes(4)
  })

  it('says it could not be kept when it is no longer being rendered and is not among those kept', async () => {
    const { calls, button, noteEl } = control({ recordings: vi.fn(async () => listed([OTHER])) })
    await recordAndStop(button)
    await pass(2000)
    expect(noteEl.textContent).toBe('The recording of Test iPhone could not be kept; SimMirror\'s log says why.')
    await pass(60_000)
    expect(calls.recordings).toHaveBeenCalledOnce()
  })

  it('is looked for at most half an hour, then left as the stop said', async () => {
    const recordings = vi.fn(async () => listed([], [FINISHING]))
    const { button, noteEl } = control({ recordings })
    await recordAndStop(button)
    await pass(30 * 60_000)
    // 2, 3, 4.5 and 6.75 seconds apart, then every 10 seconds; the first look after half an hour is the last.
    expect(recordings).toHaveBeenCalledTimes(182)
    await pass(60 * 60_000)
    expect(recordings).toHaveBeenCalledTimes(183)
    expect(noteEl.textContent).toContain('still being rendered')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('is no longer looked for once a new recording begins, here or elsewhere, or the control goes', async () => {
    let answer: (list: RecordingList) => void = () => undefined
    const pending = vi.fn(() => new Promise<RecordingList>((resolve) => { answer = resolve }))
    const here = control({ recordings: pending })
    await recordAndStop(here.button)
    await pass(2000)
    expect(pending).toHaveBeenCalledOnce()
    // A new recording, begun while a look is under way: what that look answers is not shown.
    here.button.click()
    await flush()
    answer(listed([KEPT]))
    await flush()
    expect(here.noteEl.hidden).toBe(true)
    await pass(60_000)
    expect(pending).toHaveBeenCalledOnce()
    here.made.destroy()

    const elsewhere = control()
    await recordAndStop(elsewhere.button)
    elsewhere.made.applyDevice({ recording: { ...UNDER_WAY, by: 'Claude' } } as never)
    await pass(60_000)
    expect(elsewhere.calls.recordings).not.toHaveBeenCalled()
    elsewhere.made.destroy()

    const gone = control()
    await recordAndStop(gone.button)
    gone.made.destroy()
    await pass(60_000)
    expect(gone.calls.recordings).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('is said to be by its note when the transport does not pass `rendering` on', async () => {
    const { rendering: _, ...unsaid } = RENDERING
    const { calls, button, noteEl } = control({
      stopRecording: vi.fn(async () => unsaid), recordings: vi.fn(async () => listed([KEPT])),
    })
    await recordAndStop(button)
    await pass(2000)
    expect(calls.recordings).toHaveBeenCalledOnce()
    expect(noteEl.querySelectorAll('[data-smv-save]')).toHaveLength(1)
  })

  it('is left as the stop said by a transport that cannot list recordings, and an older server is not looked for', async () => {
    const unlisted = control({ recordings: undefined })
    await recordAndStop(unlisted.button)
    await pass(60_000)
    expect(unlisted.noteEl.textContent).toContain('still being rendered')
    expect(vi.getTimerCount()).toBe(0)

    const older = control({ stopRecording: vi.fn(async () => ({ ...KEPT, files: [], notes: ['kept as recorded'] })) })
    await recordAndStop(older.button)
    await pass(60_000)
    expect(older.calls.recordings).not.toHaveBeenCalled()
  })

  it('is told apart from one kept', () => {
    expect(stillRendering(RENDERING)).toBe(true)
    expect(stillRendering({ ...RENDERING, rendering: undefined })).toBe(true)
    expect(stillRendering(KEPT)).toBe(false)
    expect(stillRendering({ ...KEPT, files: [], notes: ['it could not be rendered: no helper'] })).toBe(false)
  })
})

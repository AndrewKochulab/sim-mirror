// SPDX-License-Identifier: Apache-2.0
/**
 * The Record button: start recording the device's screen, see how long it has run, stop it, and save what was kept.
 *
 * Whether the device is being recorded is the server's to say (the device's `recording`), so a recording an agent
 * started shows here too -- by whom -- and a person may stop it. What was kept is offered in the note over the screen
 * (`note.ts`), a button per file, each saved as the file it is, with its size; the copy for a pull request says so.
 *
 * Beside the button, a menu (`popover.ts`) says how the next recording is kept: at full size as the settings say, as
 * the `pr` preset -- an MP4 small enough for a pull request -- or both. The choice holds while the viewer does.
 *
 * A recording that takes longer to render than the server waits is said to be still rendering, with no files. The
 * viewer then looks for it among the recordings kept -- every few seconds, then less often, for up to half an hour --
 * and offers its files once it is there, or says it could not be kept when it is no longer being rendered and is not.
 */
import { escapeHTML } from './escape'
import type { IconRenderer } from './icons'
import type { Note } from './note'
import { createPopover, type Layers } from './popover'
import type { Device, Recording, RecordingFile, RecordingState } from './protocol.generated'
import type { RecordingAsk, SimMirrorTransport, StoppedRecording } from './transport'

const TICK_MS = 1000
/** How long the viewer waits before it first looks for a recording still being rendered, and at most between looks. */
const LOOK_FIRST_MS = 2000
const LOOK_LONGEST_MS = 10_000
/** How long it looks before it stops: the note already says where the recording will be kept. */
const LOOK_FOR_MS = 30 * 60_000
/** How the server's note of a recording still being rendered begins, for a transport that does not pass `rendering`. */
const STILL_RENDERING = 'still being rendered'

/** The recording calls the control needs, and `recordings`, to be told when one still being rendered is kept. */
export type RecordingTransport = Required<
  Pick<SimMirrorTransport, 'startRecording' | 'stopRecording' | 'recordingFile'>
> & Pick<SimMirrorTransport, 'recordings'>

/** How Record keeps the next recording. */
export type RecordingSize = 'full' | 'pr' | 'both'

interface Size {
  label: string
  /** What it keeps, beside its label in the menu. */
  detail: string
  /** What the Record button says it will do. */
  record: string
  /** What the menu's button shows beside its caret. */
  mark: string
  ask: RecordingAsk
}

/** The sizes the menu offers. */
export const SIZES: Record<RecordingSize, Size> = {
  full: { label: 'Full size', detail: 'As the settings say', record: 'Record the screen', mark: '', ask: {} },
  pr: {
    label: 'For a pull request', detail: 'MP4, 450 wide', record: 'Record the screen for a pull request', mark: 'PR',
    ask: { preset: 'pr' },
  },
  both: {
    label: 'Both', detail: 'Full size, and a copy for a pull request',
    record: 'Record the screen, with a copy for a pull request', mark: '+PR', ask: { also: 'pr' },
  },
}

export interface RecordingControlOptions {
  button: HTMLButtonElement
  /** Where the time recorded is shown. */
  clock: HTMLElement
  /** The viewer's root, for the size menu's keys. */
  root: HTMLElement
  /** The button beside Record that opens the size menu, and the menu's panel. */
  sizeToggle: HTMLButtonElement
  sizePanel: HTMLElement
  layers: Layers
  /** The note's element, where the files kept are offered. */
  noteEl: HTMLElement
  note: Note
  transport: RecordingTransport
  icon: IconRenderer
  /** Save a file a recording was kept as; a download link clicked, unless a host saves it its own way. */
  save?(file: Blob, name: string): void
  now?(): number
}

export interface RecordingControl {
  /** Whether the device can be recorded: the button and its menu are offered only then. */
  offer(on: boolean): void
  /** How the device stands, as the server says: whether it is being recorded, since when and by whom. */
  applyDevice(device: Device | null): void
  destroy(): void
}

/** A duration as minutes and seconds: 0:07, 12:30. */
export function elapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** A file's size as a person reads it. */
export function sizeOf(bytes: number): string {
  return bytes >= 100_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`
}

/** Whether a file is the `pr` copy kept beside a recording: `<id>.pr.mp4`, as the server names it. */
export function isPrCopy(recording: Recording, file: RecordingFile): boolean {
  return file.name.startsWith(`${recording.id}.pr.`)
}

/** What a kept file's Save button says: its format, the copy for a pull request, its pixels when known, its size. */
export function saveLabel(file: RecordingFile, copy: boolean): string {
  const pixels = file.width > 0 ? ` · ${file.width}×${file.height}` : ''
  return `Save ${file.format.toUpperCase()}${copy ? ' for a pull request' : ''}${pixels} · ${sizeOf(file.bytes)}`
}

/** Whether what a stop answered is a recording still being rendered, its files not yet kept. An older server's answer
 * never is. */
export function stillRendering(recording: StoppedRecording): boolean {
  return recording.rendering === true
    || (recording.files.length === 0 && recording.notes.some((line) => line.startsWith(STILL_RENDERING)))
}

export function download(file: Blob, name: string): void {
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), TICK_MS * 60)
}

export function createRecordingControl(options: RecordingControlOptions): RecordingControl {
  const { button, clock, note, transport, icon, sizeToggle, sizePanel } = options
  const now = options.now ?? (() => Date.now())
  const save = options.save ?? download
  /** When the recording under way began, on this page's clock, and who began it; null when none is. */
  let under: { began: number, by: string } | null = null
  let timer: number | null = null
  let busy = false
  let size: RecordingSize = 'full'
  /** What this viewer asked of the recording it started; nothing for one started elsewhere. */
  let asked: RecordingAsk = {}
  /** Looking for a recording still being rendered among those kept: the next look's timer; null when not looking. */
  let looking: { timer: number } | null = null

  const menu = createPopover({ root: options.root, toggle: sizeToggle, panel: sizePanel, layers: options.layers })

  function paint(): void {
    const recording = under !== null
    const { label, mark, record } = SIZES[size]
    button.setAttribute('aria-pressed', String(recording))
    button.title = recording ? `Stop recording (started by ${under!.by})` : record
    button.setAttribute('aria-label', button.title)
    button.innerHTML = icon(recording ? 'square' : 'record')
    button.disabled = busy
    sizeToggle.title = `Recording size: ${label}`
    sizeToggle.setAttribute('aria-label', sizeToggle.title)
    sizeToggle.textContent = mark
    // The size is the next recording's: it is not changed while one is under way.
    sizeToggle.disabled = busy || recording
    if (sizeToggle.disabled) menu.close('api')
    clock.hidden = !recording
    clock.textContent = recording ? `REC ${elapsed(now() - under!.began)}` : ''
  }

  function paintSizes(): void {
    sizePanel.innerHTML = (Object.keys(SIZES) as RecordingSize[]).map((id) =>
      `<button type="button" role="menuitemradio" aria-checked="${id === size}" data-smv-size="${id}">`
      + `${SIZES[id].label}<span>${SIZES[id].detail}</span></button>`).join('')
  }

  function follow(state: RecordingState | null): void {
    // A new recording is what the note is about from now on, not one still being rendered before it.
    if (state) stopLooking()
    under = state ? { began: now() - state.since_ms, by: state.by } : null
    if (timer !== null) window.clearInterval(timer)
    timer = state ? window.setInterval(paint, TICK_MS) : null
    paint()
  }

  function kept(recording: Recording, wanted: RecordingAsk): void {
    const copies = recording.files.map((file) => isPrCopy(recording, file))
    const files = recording.files.map((file, at) =>
      `<button type="button" class="smv-btn" data-smv-save="${escapeHTML(file.name)}">`
      + `${escapeHTML(saveLabel(file, copies[at]))}</button>`).join('')
    const said = [...recording.notes]
    // A server older than the pr copy keeps the recording as its settings say, and says nothing of a copy.
    if (wanted.also && !copies.includes(true) && !said.some((line) => line.includes(`${wanted.also} copy`))) {
      said.push('No copy for a pull request was kept: this server does not make one.')
    }
    const notes = said.map((line) => `<p>${escapeHTML(line)}</p>`).join('')
    note.show(`<p>Recorded ${elapsed(recording.duration_ms)} of ${escapeHTML(recording.device)}.</p>${notes}${files}`)
  }

  function stopLooking(): void {
    if (looking !== null) window.clearTimeout(looking.timer)
    looking = null
  }

  /** Look for a recording still being rendered among those kept, until it is kept, is no longer being rendered, or
   * the wait is over; a failed look is tried again. */
  function lookFor(recording: Recording, wanted: RecordingAsk): void {
    const list = transport.recordings?.bind(transport)
    if (!list) return
    stopLooking()
    const until = now() + LOOK_FOR_MS
    const look = { timer: 0 }
    let wait = LOOK_FIRST_MS
    const once = async () => {
      const answer = await list().catch(() => null)
      if (looking !== look) return
      const done = answer?.recordings.find((one) => one.id === recording.id)
      // An older server lists none still being rendered, so says nothing of one that is no longer.
      const gone = !done && answer?.finishing !== undefined && !answer.finishing.some((one) => one.id === recording.id)
      if (done) kept(done, wanted)
      else if (gone) note.sayText(`The recording of ${recording.device} could not be kept; SimMirror's log says why.`)
      looking = done || gone || now() >= until ? null : look
      if (looking) again()
    }
    const again = () => {
      look.timer = window.setTimeout(() => void once(), wait)
      wait = Math.min(wait * 1.5, LOOK_LONGEST_MS)
    }
    looking = look
    again()
  }

  async function press(): Promise<void> {
    busy = true
    paint()
    try {
      if (under) {
        const wanted = asked
        const recording = await transport.stopRecording()
        asked = {}
        follow(null)
        kept(recording, wanted)
        if (stillRendering(recording)) lookFor(recording, wanted)
      } else {
        note.hide()
        const ask = SIZES[size].ask
        follow(await transport.startRecording(undefined, ask))
        asked = ask
      }
    } catch (error) {
      note.sayText((error as Error).message || 'The recording could not be made.')
    } finally {
      busy = false
      paint()
    }
  }

  async function saveFile(name: string): Promise<void> {
    try {
      save(await transport.recordingFile(name), name)
    } catch (error) {
      note.sayText((error as Error).message || `${name} could not be saved.`)
    }
  }

  const onButton = () => void press()
  const onNote = (event: Event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-smv-save]')
    if (target) void saveFile(target.dataset.smvSave!)
  }
  const onToggle = () => {
    if (menu.isOpen) return menu.close('toggle')
    menu.open()
    paintSizes()
    menu.focusItem('checked')
  }
  const onSizes = (event: Event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-smv-size]')
    if (!target) return
    size = target.dataset.smvSize as RecordingSize
    menu.close('api')
    paint()
    sizeToggle.focus()
  }
  button.addEventListener('click', onButton)
  sizeToggle.addEventListener('click', onToggle)
  sizePanel.addEventListener('click', onSizes)
  options.noteEl.addEventListener('click', onNote)
  paint()

  return {
    offer(on) {
      button.hidden = !on
      sizeToggle.hidden = !on
      if (!on) menu.close('api')
    },
    applyDevice(device) {
      const state = device?.recording ?? null
      if ((state === null) !== (under === null) || (state && under && state.by !== under.by)) {
        // One that stopped or began elsewhere is not the one this viewer asked for.
        asked = {}
        follow(state)
      }
    },
    destroy() {
      if (timer !== null) window.clearInterval(timer)
      stopLooking()
      button.removeEventListener('click', onButton)
      sizeToggle.removeEventListener('click', onToggle)
      sizePanel.removeEventListener('click', onSizes)
      options.noteEl.removeEventListener('click', onNote)
      menu.destroy()
    },
  }
}

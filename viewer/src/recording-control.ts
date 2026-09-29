// SPDX-License-Identifier: Apache-2.0
/**
 * The Record button: start recording the device's screen, see how long it has run, stop it, and save what was kept.
 *
 * Whether the device is being recorded is the server's to say (the device's `recording`), so a recording an agent
 * started shows here too -- by whom -- and a person may stop it. What was kept is offered in the note over the screen
 * (`note.ts`), a button per file, each saved as the file it is.
 */
import { escapeHTML } from './escape'
import type { IconRenderer } from './icons'
import type { Note } from './note'
import type { Device, Recording, RecordingState } from './protocol.generated'
import type { SimMirrorTransport } from './transport'

const TICK_MS = 1000

export type RecordingTransport = Required<
  Pick<SimMirrorTransport, 'startRecording' | 'stopRecording' | 'recordingFile'>
>

export interface RecordingControlOptions {
  button: HTMLButtonElement
  /** Where the time recorded is shown. */
  clock: HTMLElement
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

export function download(file: Blob, name: string): void {
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), TICK_MS * 60)
}

export function createRecordingControl(options: RecordingControlOptions): RecordingControl {
  const { button, clock, note, transport, icon } = options
  const now = options.now ?? (() => Date.now())
  const save = options.save ?? download
  /** When the recording under way began, on this page's clock, and who began it; null when none is. */
  let under: { began: number, by: string } | null = null
  let timer: number | null = null
  let busy = false

  function paint(): void {
    const recording = under !== null
    button.setAttribute('aria-pressed', String(recording))
    button.title = recording ? `Stop recording (started by ${under!.by})` : 'Record the screen'
    button.setAttribute('aria-label', button.title)
    button.innerHTML = icon(recording ? 'square' : 'record')
    button.disabled = busy
    clock.hidden = !recording
    clock.textContent = recording ? `REC ${elapsed(now() - under!.began)}` : ''
  }

  function follow(state: RecordingState | null): void {
    under = state ? { began: now() - state.since_ms, by: state.by } : null
    if (timer !== null) window.clearInterval(timer)
    timer = state ? window.setInterval(paint, TICK_MS) : null
    paint()
  }

  function kept(recording: Recording): void {
    const files = recording.files.map((file) =>
      `<button type="button" class="smv-btn" data-smv-save="${escapeHTML(file.name)}">`
      + `Save ${file.format.toUpperCase()} · ${sizeOf(file.bytes)}</button>`).join('')
    const notes = recording.notes.map((said) => `<p>${escapeHTML(said)}</p>`).join('')
    note.show(`<p>Recorded ${elapsed(recording.duration_ms)} of ${escapeHTML(recording.device)}.</p>${notes}${files}`)
  }

  async function press(): Promise<void> {
    busy = true
    paint()
    try {
      if (under) {
        const recording = await transport.stopRecording()
        follow(null)
        kept(recording)
      } else {
        note.hide()
        follow(await transport.startRecording())
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
  button.addEventListener('click', onButton)
  options.noteEl.addEventListener('click', onNote)
  paint()

  return {
    applyDevice(device) {
      const state = device?.recording ?? null
      if ((state === null) !== (under === null) || (state && under && state.by !== under.by)) follow(state)
    },
    destroy() {
      if (timer !== null) window.clearInterval(timer)
      button.removeEventListener('click', onButton)
      options.noteEl.removeEventListener('click', onNote)
    },
  }
}

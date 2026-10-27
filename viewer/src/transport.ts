// SPDX-License-Identifier: Apache-2.0
/**
 * How the viewer reaches its server: a scope's HTTP routes, and its screen socket's URL.
 *
 * `createHttpTransport` talks to a SimMirror daemon or any host that mounts SimMirror's routers. A host with its own
 * API client -- its own authentication, its own error handling -- passes an object of this shape instead.
 */
import type {
  DeviceChoice, Recording, RecordingList, RecordingState, ScopeStatus, SettingsChange, SettingsView, Started,
  TouchSetup,
} from './protocol.generated'

/** A change to how the device looks, named as an agent's `sim_device` names it: `{ action: 'text_size', size: 'large' }`. */
export type DeviceChange = { action: string } & Record<string, string | number | boolean>

/** What a recording is kept as. */
export type RecordingFormat = 'mp4' | 'gif' | 'both'

/** What else a recording is asked for: kept as the `pr` preset in place of the settings, or with a `pr` copy beside
 * what the settings keep. A server older than these keeps it as the settings say. */
export interface RecordingAsk {
  preset?: 'pr'
  also?: 'pr'
}

/** What stopping a recording answers: what was kept -- or, with `rendering`, what it is so far, with no files yet,
 * which are kept and listed by `recordings` once it is rendered. A transport that does not pass `rendering` on, or an
 * older server, leaves it out. */
export type StoppedRecording = Recording & { rendering?: boolean }

export interface SimMirrorTransport {
  /** Whether the scope can have a simulator, why not, and how its device stands. */
  status(): Promise<ScopeStatus>
  /** Bring the device up if needed, and mint a one-shot ticket for its screen. Rejects with a message worth showing. */
  start(): Promise<Started>
  /** Let the device go; with `shutdown`, shut it down too. Answers whether one was running. */
  stop(shutdown: boolean): Promise<boolean>
  /** This Mac's simulators the scope could use. */
  devices(): Promise<DeviceChoice[]>
  /** Use this simulator for the scope from now on. */
  choose(udid: string): Promise<void>
  /** The screen socket's URL for a ticket. */
  socketUrl(ticket: string): string
  /** Every setting as the scope sees it. A transport without it offers no settings panel. */
  settings?(): Promise<SettingsView>
  /** Change settings, all or none, answering them as they now are. Rejects with the server's `SettingsRefusal` as the
   * error's `body`: each setting's problem, or the confirmation a sensitive change waits for. */
  changeSettings?(change: SettingsChange): Promise<SettingsView>
  /** Start recording the device's screen, kept as the settings say unless `format` or `ask` says otherwise. A
   * transport without the recording methods offers no Record button. */
  startRecording?(format?: RecordingFormat, ask?: RecordingAsk): Promise<RecordingState>
  /** Stop recording and keep it, answering what was kept -- or that it is still being rendered. */
  stopRecording?(): Promise<StoppedRecording>
  /** One of a kept recording's files, to save. */
  recordingFile?(name: string): Promise<Blob>
  /** The recordings kept, the one under way, and those still being rendered. A transport without it leaves a
   * recording still being rendered when it stopped as the stop said: the viewer is not told when it is kept. */
  recordings?(): Promise<RecordingList>
  /** Change how the device looks, answering what was done. A transport without it offers no Display menu. */
  changeDevice?(change: DeviceChange): Promise<string>
  /** Whether a real device can be touched, and what a person can do. A transport without it and `setUpTouch` offers
   * no Set up touch. */
  touch?(): Promise<TouchSetup>
  /** Set touching a real device up -- build WebDriverAgent for it, or start it again -- answering how it then stands. */
  setUpTouch?(): Promise<TouchSetup>
}

// SPDX-License-Identifier: Apache-2.0
/**
 * The viewer's connection to a device's screen: a ticket, a hello each way, then frames, events and input.
 *
 * On connecting the server says what it offers -- encodings, its connector and what that can do -- and the viewer
 * answers with what it decodes: H.264 first where this page can (WebCodecs, in a secure page) and it has not failed
 * here before, then JPEG. The server picks and says which. Frames come tagged with their encoding; JSON carries the
 * device's state, what an agent is about to do, and the text read from the screen's pixels.
 *
 * A live device's connection that drops -- a server restart, a network blip -- is tried again by itself a few times
 * (`RECONNECT_MS`) before the viewer asks; a device being restarted is always reconnected to. A device switched off
 * (4403) or stopped (4410), or a server that does not speak this protocol (4400, 4406), is not.
 */
import { readAgentEvent, type AgentCursorHandle } from './agent-cursor'
import { createH264Sink, type H264Sink } from './h264-decoder'
import {
  CLOSE_BAD_MESSAGE, CLOSE_FORBIDDEN, CLOSE_RESTARTING, CLOSE_STOPPED, CLOSE_UNSUPPORTED, PROTOCOL_VERSION, TAG_H264,
  TAG_JPEG, type Capability, type ClientHello, type ClientInput, type Device, type Encoding, type ServerHello,
  type Size,
} from './protocol.generated'
import type { ScreenCanvas } from './screen-canvas'
import { readDevice, type StatusView } from './status-view'
import { readScreenText, type TextOverlayHandle } from './text-overlay'
import { createTicketedSocket } from './ticketed-socket'
import type { SimMirrorTransport } from './transport'

/** How long a dropped connection to a live device waits before each attempt to reconnect by itself. */
export const RECONNECT_MS: readonly number[] = [1000, 3000, 10000]

const strings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string')

/** The server's hello, checked -- or null for anything that is not one this viewer speaks. */
export function readServerHello(body: Record<string, unknown>): ServerHello | null {
  if (body.type !== 'hello' || body.v !== PROTOCOL_VERSION || !strings(body.encodings) || !strings(body.capabilities)
      || typeof body.connector !== 'string' || !(body.fallback_reason === null || typeof body.fallback_reason === 'string')) {
    return null
  }
  return {
    type: 'hello', v: PROTOCOL_VERSION, server: typeof body.server === 'string' ? body.server : '',
    encodings: body.encodings as Encoding[], connector: body.connector,
    capabilities: body.capabilities as Capability[], fallback_reason: body.fallback_reason,
  }
}

export interface StreamOptions {
  transport: SimMirrorTransport
  screen: ScreenCanvas
  cursor: AgentCursorHandle
  /** The text read from the screen's pixels, outlined over it. */
  text: TextOverlayHandle
  status: StatusView
  canDecodeH264(): boolean
  /** Whether the viewer still wants a connection: active, and not destroyed. */
  wanted(): boolean
  /** The screen's shape, for drawing frames in its units. */
  unitsOf(): Size
  onHello(hello: ServerHello): void
  /** Something a host may want to know about changed: the device, the hello, the connection. */
  onChange(): void
  /** The connection ended, however it ended. */
  onEnd(): void
}

export interface ViewerStream {
  readonly device: Device | null
  readonly hello: ServerHello | null
  readonly encoding: Encoding | null
  readonly open: boolean
  /** Whether the device's connector can do this; nothing is allowed before the server says. */
  allows(capability: Capability): boolean
  connect(): Promise<void>
  close(): void
  /** Start afresh on a person's request: attempts counted from zero, and any socket let go first. */
  restart(): void
  send(message: ClientInput): boolean
  cancelReconnect(): void
}

export function createViewerStream(options: StreamOptions): ViewerStream {
  const { transport, screen, cursor, text, status } = options
  let device: Device | null = null
  let hello: ServerHello | null = null
  let encoding: Encoding | null = null
  let reconnects = 0
  let reconnectTimer: number | null = null
  let dropped: string | null = null
  /** H.264 failed on this page once: JPEG from then on. */
  let h264Failed = false
  let sink: H264Sink | null = null

  function applyDevice(next: Device | null): void {
    device = next
    if (next?.state === 'ready') {
      reconnects = 0
      dropped = null
    } else {
      // A screen that is not showing is no screen to outline text on.
      text.clear()
    }
    status.applyDevice(next)
    options.onChange()
  }

  function cancelReconnect(): void {
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
    reconnectTimer = null
    dropped = null
  }

  /** Try again in a while, when there are attempts left; answers whether one was planned. */
  function reconnectLater(why: string): boolean {
    if (!options.wanted() || reconnects >= RECONNECT_MS.length) return false
    dropped = why
    status.showState('stalled')
    status.say(`${why} Reconnecting…`)
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      void connect()
    }, RECONNECT_MS[reconnects++])
    return true
  }

  function dropSink(): void {
    sink?.close()
    sink = null
  }

  /** This connection's H.264 decoder, made with its first frame. A new connection starts at a key frame. */
  function h264(): H264Sink {
    sink ??= createH264Sink({
      draw: (frame) => screen.drawFrame(frame, frame.displayWidth, frame.displayHeight, options.unitsOf()),
      onError: () => {
        // A stream this page cannot decode is no reason to show nothing: JPEG instead.
        h264Failed = true
        dropSink()
        socket.close()
        void connect()
      },
    })
    return sink
  }

  function heard(event: MessageEvent): void {
    if (event.data instanceof ArrayBuffer) {
      const bytes = new Uint8Array(event.data)
      if (bytes.length < 2) return
      if (bytes[0] === TAG_JPEG) screen.drawBytes(event.data.slice(1), options.unitsOf())
      else if (bytes[0] === TAG_H264) h264().push(bytes.subarray(1))
      return
    }
    let message: unknown
    try {
      message = JSON.parse(String(event.data))
    } catch {
      return
    }
    if (!message || typeof message !== 'object') return
    const body = message as Record<string, unknown>
    if (body.type === 'hello') {
      const said = readServerHello(body)
      if (!said) return
      hello = said
      options.onHello(said)
      options.onChange()
    } else if (body.type === 'stream') {
      if (body.encoding === 'h264' || body.encoding === 'jpeg') encoding = body.encoding
    } else if (body.type === 'status') {
      const next = readDevice(body)
      if (next) applyDevice(next)
    } else if (body.type === 'agent') {
      const agent = readAgentEvent(body)
      if (agent) cursor.handle(agent)
    } else if (body.type === 'screen_text') {
      const read = readScreenText(body)
      if (read) text.show(read)
    }
  }

  function stop(message: string, offer: 'start' | 'retry'): void {
    cancelReconnect()
    status.showState('stopped')
    status.say(message, offer)
    options.onChange()
  }

  function ended(event: CloseEvent): void {
    dropSink()
    status.stopBootTimer()
    text.clear()
    encoding = null
    options.onEnd()
    // The server closes a socket before the last status could reach it, so the pill follows the close itself.
    if (event.code === CLOSE_STOPPED) return stop('The simulator stopped.', 'start')
    // Switched off -- but it may be switched on again, and nothing else here would notice: trying again only asks.
    if (event.code === CLOSE_FORBIDDEN) return stop(event.reason || 'The simulator is off here.', 'retry')
    if (event.code === CLOSE_UNSUPPORTED || event.code === CLOSE_BAD_MESSAGE) {
      return stop(event.reason || 'This viewer and the server do not speak the same protocol.', 'retry')
    }
    // A restart will bring the device back: not the dropped connection a count of attempts guards against.
    const restarting = event.code === CLOSE_RESTARTING
    if (restarting) reconnects = 0
    const why = restarting ? 'The simulator is restarting.'
      : event.reason ? `The simulator's screen closed: ${event.reason}` : 'The connection to the simulator ended.'
    // A restart says "stopped" before it closes each screen in turn, so its close is live whatever the last status said.
    const wasLive = restarting || (device !== null && device.state !== 'failed' && device.state !== 'stopped')
    if (wasLive && reconnectLater(why)) return
    stop(why, 'retry')
  }

  const socket = createTicketedSocket({
    binaryType: 'arraybuffer',
    mint: async () => {
      const started = await transport.start()
      // Closed or put away while the start was asked for: nothing is painted and no timer starts on a view that is gone.
      if (!options.wanted()) throw new Error('the simulator view was closed')
      applyDevice(started.device)
      return started.ticket
    },
    url: (ticket) => transport.socketUrl(ticket),
    onOpen: () => {
      const encodings: Encoding[] = !h264Failed && options.canDecodeH264() ? ['h264', 'jpeg'] : ['jpeg']
      const answer: ClientHello = { type: 'hello', v: PROTOCOL_VERSION, encodings }
      socket.sendJson(answer)
      options.onChange()
    },
    onMessage: heard,
    onEnd: ended,
    onRefused: (err) => {
      // While reconnecting, a refused start is the server still away: keep trying while there are attempts left.
      if (dropped !== null && reconnectLater(dropped)) return
      cancelReconnect()
      status.showState('failed')
      status.say(err.message || 'The simulator could not start.', 'retry')
      options.onChange()
    },
  })

  async function connect(): Promise<void> {
    if (options.wanted()) await socket.connect()
  }

  return {
    get device() {
      return device
    },
    get hello() {
      return hello
    },
    get encoding() {
      return encoding
    },
    get open() {
      return socket.open
    },
    allows: (capability) => hello?.capabilities.includes(capability) ?? false,
    connect,
    close() {
      dropSink()
      socket.close()
    },
    restart() {
      cancelReconnect()
      reconnects = 0
      // A device that failed to start keeps its socket open, waiting for a ready that will not come; asking again
      // through it would open nothing, so it is let go first and the start is asked for afresh.
      socket.close()
      void connect()
    },
    send: (message) => socket.sendJson(message),
    cancelReconnect,
  }
}

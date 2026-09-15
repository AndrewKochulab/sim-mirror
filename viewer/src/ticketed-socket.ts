// SPDX-License-Identifier: Apache-2.0
/**
 * A socket that spends a one-shot ticket on every connect: a device's screen socket.
 *
 * A ticket is minted over HTTP and spent on connecting, because a WebSocket cannot carry a token in a header. A ticket
 * that expired in between -- the server closes with 4401 -- is worth exactly one quiet retry with a fresh one; a second
 * is an ending.
 *
 * It connects only while wanted. `connect()` says it is and `close()` says it is not, so a mint that comes back after
 * `close()` opens nothing, and a second `connect()` while the first is minting opens one socket, not two.
 */
import { CLOSE_UNAUTHORIZED } from './protocol.generated'

export interface TicketedSocketOptions {
  /** Mint a ticket; rejects, with a message worth showing, when there is none to have. */
  mint(): Promise<string>
  /** The socket's URL for a ticket. */
  url(ticket: string): string
  /** `arraybuffer` for a screen that sends its frames as bytes. */
  binaryType?: BinaryType
  onOpen?(): void
  onMessage(event: MessageEvent): void
  /** The connection ended by itself: not by `close()`, and not by the one quiet retry. */
  onEnd(event: CloseEvent): void
  /** No ticket could be minted, so nothing was opened. */
  onRefused(error: Error): void
}

export interface TicketedSocket {
  connect(): Promise<void>
  close(): void
  /** Send when open; answers whether it was sent. */
  send(data: string | ArrayBufferLike | ArrayBufferView | Blob): boolean
  sendJson(message: object): boolean
  /** Open and able to send. */
  readonly open: boolean
  /** Connecting or open. */
  readonly live: boolean
}

export function createTicketedSocket(options: TicketedSocketOptions): TicketedSocket {
  let socket: WebSocket | null = null
  let wanted = false
  let retried = false
  /** Which connect is current: a mint that returns for an older one opens nothing. */
  let attempt = 0

  async function connect(): Promise<void> {
    wanted = true
    if (socket) return
    const mine = ++attempt
    let ticket: string
    try {
      ticket = await options.mint()
    } catch (err) {
      if (mine === attempt && wanted) options.onRefused(err instanceof Error ? err : new Error(String(err)))
      return
    }
    if (mine !== attempt || !wanted || socket) return
    const ws = new WebSocket(options.url(ticket))
    if (options.binaryType) ws.binaryType = options.binaryType
    socket = ws
    ws.onopen = () => {
      if (socket !== ws) return
      retried = false
      options.onOpen?.()
    }
    ws.onmessage = (event) => {
      if (socket === ws) options.onMessage(event)
    }
    ws.onclose = (event) => {
      // `close()` lets go of its socket first, so only an ending nobody asked for gets past this.
      if (socket !== ws) return
      socket = null
      if (event.code === CLOSE_UNAUTHORIZED && !retried) {
        retried = true
        void connect()
        return
      }
      options.onEnd(event)
    }
  }

  function send(data: string | ArrayBufferLike | ArrayBufferView | Blob): boolean {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false
    socket.send(data as string)
    return true
  }

  return {
    connect,
    close() {
      wanted = false
      attempt++
      const ws = socket
      socket = null
      ws?.close()
    },
    send,
    sendJson: (message: object) => send(JSON.stringify(message)),
    get open() {
      return socket !== null && socket.readyState === WebSocket.OPEN
    },
    get live() {
      return socket !== null
    },
  }
}

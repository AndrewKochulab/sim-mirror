// SPDX-License-Identifier: Apache-2.0
/**
 * A WebSocket a test drives by hand: it opens, speaks and ends when told.
 *
 * Stub it in for the real one with `vi.stubGlobal('WebSocket', FakeSocket)`. JSON the viewer sends is recorded parsed;
 * bytes are recorded as they are.
 */
export class FakeSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 3
  static instances: FakeSocket[] = []

  static last(): FakeSocket {
    return FakeSocket.instances[FakeSocket.instances.length - 1]
  }

  static reset(): void {
    FakeSocket.instances = []
  }

  readyState = FakeSocket.CONNECTING
  binaryType: BinaryType = 'blob'
  sent: unknown[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null

  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }

  send(data: unknown): void {
    this.sent.push(typeof data === 'string' ? JSON.parse(data) : data)
  }

  close(): void {
    this.closed = true
    this.readyState = FakeSocket.CLOSED
  }

  open(): void {
    this.readyState = FakeSocket.OPEN
    this.onopen?.()
  }

  /** A message from the server: bytes as they are, a string as it is, anything else as JSON. */
  message(payload: unknown): void {
    const data = typeof payload === 'string' || payload instanceof ArrayBuffer ? payload : JSON.stringify(payload)
    this.onmessage?.({ data })
  }

  end(code = 1000, reason = ''): void {
    this.readyState = FakeSocket.CLOSED
    this.onclose?.({ code, reason })
  }
}

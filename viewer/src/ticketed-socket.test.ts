// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeSocket } from '../test-support/fake-socket'
import { CLOSE_UNAUTHORIZED } from './protocol.generated'
import { createTicketedSocket, type TicketedSocketOptions } from './ticketed-socket'

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve() }

function setup(overrides: Partial<TicketedSocketOptions> = {}) {
  let n = 0
  const options: TicketedSocketOptions = {
    mint: vi.fn(async () => `tk${++n}`),
    url: (ticket) => `ws://x/screen?ticket=${ticket}`,
    onOpen: vi.fn(),
    onMessage: vi.fn(),
    onEnd: vi.fn(),
    onRefused: vi.fn(),
    ...overrides,
  }
  return { options, socket: createTicketedSocket(options) }
}

beforeEach(() => {
  FakeSocket.reset()
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createTicketedSocket', () => {
  it('connects with a fresh ticket, says when it opens, and passes messages on', async () => {
    const { options, socket } = setup({ binaryType: 'arraybuffer' })
    expect(socket.live).toBe(false)
    await socket.connect()
    const ws = FakeSocket.last()
    expect(ws.url).toBe('ws://x/screen?ticket=tk1')
    expect(ws.binaryType).toBe('arraybuffer')
    expect(socket.live).toBe(true)
    expect(socket.open).toBe(false)
    expect(socket.sendJson({ type: 'early' })).toBe(false)
    ws.open()
    expect(options.onOpen).toHaveBeenCalledOnce()
    expect(socket.open).toBe(true)
    expect(socket.sendJson({ type: 'touch' })).toBe(true)
    const bytes = new ArrayBuffer(2)
    expect(socket.send(bytes)).toBe(true)
    expect(ws.sent).toEqual([{ type: 'touch' }, bytes])
    ws.message({ type: 'status' })
    expect(options.onMessage).toHaveBeenCalledWith({ data: '{"type":"status"}' })
  })

  it('keeps the binary type the browser chose when none is asked for, and opens without an onOpen', async () => {
    const { socket } = setup({ onOpen: undefined })
    await socket.connect()
    FakeSocket.last().open()
    expect(FakeSocket.last().binaryType).toBe('blob')
    expect(socket.open).toBe(true)
  })

  it('retries an expired ticket once, quietly, and ends on a second refusal', async () => {
    const { options, socket } = setup()
    await socket.connect()
    FakeSocket.last().end(CLOSE_UNAUTHORIZED)
    await flush()
    expect(FakeSocket.instances).toHaveLength(2)
    expect(FakeSocket.last().url).toContain('tk2')
    expect(options.onEnd).not.toHaveBeenCalled()
    FakeSocket.last().end(CLOSE_UNAUTHORIZED, 'no ticket')
    await flush()
    expect(FakeSocket.instances).toHaveLength(2)
    expect(options.onEnd).toHaveBeenCalledWith({ code: CLOSE_UNAUTHORIZED, reason: 'no ticket' })
    expect(socket.live).toBe(false)
  })

  it('earns another quiet retry once a connection has opened', async () => {
    const { options, socket } = setup()
    await socket.connect()
    FakeSocket.last().end(CLOSE_UNAUTHORIZED)
    await flush()
    FakeSocket.last().open()
    FakeSocket.last().end(CLOSE_UNAUTHORIZED)
    await flush()
    expect(FakeSocket.instances).toHaveLength(3)
    expect(options.onEnd).not.toHaveBeenCalled()
  })

  it('says when a connection ends by itself, and can be connected again', async () => {
    const { options, socket } = setup()
    await socket.connect()
    FakeSocket.last().open()
    FakeSocket.last().end(1014, 'companion gone')
    expect(options.onEnd).toHaveBeenCalledWith({ code: 1014, reason: 'companion gone' })
    await socket.connect()
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('closing is not an ending, and a closed socket is not heard from again', async () => {
    const { options, socket } = setup()
    await socket.connect()
    const ws = FakeSocket.last()
    ws.open()
    socket.close()
    expect(ws.closed).toBe(true)
    ws.message({ type: 'late' })
    ws.open()
    ws.end(1000)
    expect(options.onMessage).not.toHaveBeenCalled()
    expect(options.onOpen).toHaveBeenCalledOnce()
    expect(options.onEnd).not.toHaveBeenCalled()
    expect(socket.open).toBe(false)
    expect(socket.send('x')).toBe(false)
    socket.close()
  })

  it('opens nothing when closed while a ticket is being minted, and only one socket for two connects', async () => {
    let release: (ticket: string) => void = () => {}
    const { options, socket } = setup({ mint: vi.fn(() => new Promise<string>((resolve) => { release = resolve })) })
    const pending = socket.connect()
    socket.close()
    release('tk')
    await pending
    expect(FakeSocket.instances).toHaveLength(0)

    const releases: Array<(ticket: string) => void> = []
    options.mint = vi.fn(() => new Promise<string>((resolve) => { releases.push(resolve) }))
    const again = createTicketedSocket(options)
    const first = again.connect()
    const second = again.connect()
    releases[1]('newer')
    releases[0]('older')
    await Promise.all([first, second])
    expect(FakeSocket.instances.map((ws) => ws.url)).toEqual(['ws://x/screen?ticket=newer'])
    await again.connect()
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('says why when no ticket can be minted, unless it was closed meanwhile', async () => {
    const { options, socket } = setup({ mint: vi.fn(async () => { throw new Error('Simulator is off') }) })
    await socket.connect()
    expect(options.onRefused).toHaveBeenCalledWith(new Error('Simulator is off'))
    options.mint = vi.fn(async () => { throw 'plain' })
    const other = createTicketedSocket(options)
    await other.connect()
    expect(options.onRefused).toHaveBeenLastCalledWith(new Error('plain'))

    let fail: (error: Error) => void = () => {}
    options.mint = vi.fn(() => new Promise<string>((_, reject) => { fail = reject }))
    const closing = createTicketedSocket(options)
    const pending = closing.connect()
    closing.close()
    fail(new Error('too late'))
    await pending
    expect(options.onRefused).toHaveBeenCalledTimes(2)
    expect(FakeSocket.instances).toHaveLength(0)
  })
})

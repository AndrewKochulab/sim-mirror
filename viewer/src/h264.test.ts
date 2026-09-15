// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DECODE_QUEUE_MAX, createH264Sink, type H264SinkOptions } from './h264-decoder'
import { AccessUnitReader, BUFFER_MAX, canDecodeH264, codecString, type AccessUnit } from './h264-stream'

const LONG = [0, 0, 0, 1]
const SHORT = [0, 0, 1]
/** High profile, level 3.1: `avc1.64001f`. */
const SPS = [...LONG, 0x67, 0x64, 0x00, 0x1f, 0xac, 0xd9]
const PPS = [...LONG, 0x68, 0xeb, 0xe3]
/** A slice whose first bit is set starts a picture (first_mb_in_slice = 0); one without continues it. */
const IDR = (first = true) => [...SHORT, 0x65, first ? 0x88 : 0x08, 0x84, 0x21]
const P = (first = true) => [...SHORT, 0x41, first ? 0x9a : 0x1a, 0x02, 0x03]
const SEI = [...SHORT, 0x06, 0x05, 0x10]
const AUD = [...SHORT, 0x09, 0xf0]

const bytes = (...parts: number[][]) => new Uint8Array(parts.flat())
const shape = (units: AccessUnit[]) => units.map((unit) => ({ key: unit.key, sps: unit.sps !== null, size: unit.data.length }))

describe('AccessUnitReader', () => {
  // The last picture is held until the next one starts, so each stream ends with one more.
  const stream = bytes(SPS, PPS, IDR(), P(), P(), SPS, PPS, IDR(), P(), P())

  it('groups units into pictures, marks where a decoder can start, and holds the last until it is whole', () => {
    const units = new AccessUnitReader().push(stream)
    expect(shape(units)).toEqual([
      { key: true, sps: true, size: SPS.length + PPS.length + IDR().length },
      { key: false, sps: false, size: P().length },
      { key: false, sps: false, size: P().length },
      { key: true, sps: true, size: SPS.length + PPS.length + IDR().length },
    ])
    expect(Array.from(units[0].data)).toEqual([...SPS, ...PPS, ...IDR()])
    expect(Array.from(units[0].sps!)).toEqual(SPS.slice(4))
  })

  it('finds the same pictures however the stream was cut', () => {
    const whole = shape(new AccessUnitReader().push(stream))
    expect(whole).toHaveLength(4)
    for (const size of [1, 2, 3, 5, 7]) {
      const reader = new AccessUnitReader()
      const found: AccessUnit[] = []
      for (let at = 0; at < stream.length; at += size) found.push(...reader.push(stream.subarray(at, at + size)))
      expect(shape(found), `cut every ${size}`).toEqual(whole)
    }
  })

  it('keeps the slices of one picture together, and starts a new one at anything but a slice', () => {
    const units = new AccessUnitReader().push(bytes(IDR(), IDR(false), SEI, P(), P(false), P(), P()))
    expect(shape(units)).toEqual([
      { key: true, sps: false, size: IDR().length * 2 },
      { key: false, sps: false, size: SEI.length + P().length * 2 },
    ])
  })

  it('ignores what comes before the first start code, and drops a buffer that never finds one', () => {
    expect(shape(new AccessUnitReader().push(bytes([9, 9, 9], IDR(), P(), P()))))
      .toEqual([{ key: true, sps: false, size: IDR().length }])
    const lost = new AccessUnitReader()
    expect(lost.push(new Uint8Array(BUFFER_MAX + 1).fill(7))).toEqual([])
    expect(shape(lost.push(bytes(IDR(), P(), P())))).toEqual([{ key: true, sps: false, size: IDR().length }])
    const waiting = new AccessUnitReader()
    expect(waiting.push(bytes([5, 5]))).toEqual([])
    expect(shape(waiting.push(bytes(IDR(), P(), P())))).toEqual([{ key: true, sps: false, size: IDR().length }])
  })

  it('reads a slice with nothing after its header as part of the picture before it', () => {
    const units = new AccessUnitReader().push(bytes(IDR(), [...SHORT, 0x41], P(), AUD, AUD))
    expect(shape(units)).toEqual([
      { key: true, sps: false, size: IDR().length + SHORT.length + 1 },
      { key: false, sps: false, size: P().length },
    ])
  })
})

describe('codecString and canDecodeH264', () => {
  const Decoder = function VideoDecoder() {}
  const Chunk = function EncodedVideoChunk() {}

  it('names the profile, constraints and level of a sequence parameter set', () => {
    expect(codecString(new Uint8Array(SPS.slice(4)))).toBe('avc1.64001f')
    expect(codecString(new Uint8Array([0x67, 0x42, 0xc0]))).toBe('avc1.42c000')
    expect(codecString(new Uint8Array([]))).toBe('avc1.000000')
  })

  it('needs WebCodecs, and a secure page to have it', () => {
    expect(canDecodeH264({ VideoDecoder: Decoder, EncodedVideoChunk: Chunk, isSecureContext: true })).toBe(true)
    expect(canDecodeH264({ VideoDecoder: Decoder, EncodedVideoChunk: Chunk, isSecureContext: false })).toBe(false)
    expect(canDecodeH264({ EncodedVideoChunk: Chunk, isSecureContext: true })).toBe(false)
    expect(canDecodeH264({ VideoDecoder: Decoder, isSecureContext: true })).toBe(false)
    expect(typeof canDecodeH264()).toBe('boolean')
  })
})

const D_SPS = [...LONG, 0x67, 0x64, 0x00, 0x1f, 0xac]
const D_PPS = [...LONG, 0x68, 0xeb]
const D_IDR = [...SHORT, 0x65, 0x88, 0x84]
const D_P = [...SHORT, 0x41, 0x9a, 0x02]
/** A key picture, then the start of the next picture -- which is what shows the key picture is whole. */
const KEY = () => bytes(D_SPS, D_PPS, D_IDR, D_P, D_P)

class FakeChunk {
  constructor(public init: { type: string; timestamp: number; data: Uint8Array }) {}
}

class FakeDecoder {
  static all: FakeDecoder[] = []
  static failConfigure = false
  state = 'unconfigured'
  decodeQueueSize = 0
  config: unknown = null
  chunks: FakeChunk[] = []
  failDecode = false
  constructor(public init: { output: (frame: unknown) => void; error: (e: unknown) => void }) {
    FakeDecoder.all.push(this)
  }
  configure(config: unknown) {
    if (FakeDecoder.failConfigure) throw new Error('unsupported codec')
    this.config = config
    this.state = 'configured'
  }
  decode(chunk: FakeChunk) {
    if (this.failDecode) throw new Error('bad picture')
    this.chunks.push(chunk)
  }
  close() {
    this.state = 'closed'
  }
}

function rig(overrides: Partial<H264SinkOptions> = {}) {
  const draw = vi.fn()
  const onError = vi.fn()
  return { draw, onError, sink: createH264Sink({ draw, onError, fps: 50, ...overrides }) }
}

const decoder = () => FakeDecoder.all[0]
const decoded = () => decoder().chunks.map((chunk) => [chunk.init.type, chunk.init.timestamp])

describe('createH264Sink', () => {
  beforeEach(() => {
    FakeDecoder.all = []
    FakeDecoder.failConfigure = false
    vi.stubGlobal('VideoDecoder', FakeDecoder)
    vi.stubGlobal('EncodedVideoChunk', FakeChunk)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('waits for a sequence parameter set, configures once, and decodes from the first key picture', () => {
    const { sink } = rig()
    sink.push(bytes(D_P, D_P))
    expect(FakeDecoder.all).toHaveLength(0)
    sink.push(KEY())
    expect(FakeDecoder.all).toHaveLength(1)
    expect(decoder().config).toEqual({ codec: 'avc1.64001f', optimizeForLatency: true })
    expect(decoded()).toEqual([['key', 0]])
    expect(Array.from(decoder().chunks[0].init.data)).toEqual([...D_SPS, ...D_PPS, ...D_IDR])
    sink.push(bytes(D_P))
    expect(decoded()).toEqual([['key', 0], ['delta', 20_000]])
    sink.push(KEY())
    expect(FakeDecoder.all).toHaveLength(1)
  })

  it('stamps pictures at 30 a second when not told the rate', () => {
    const { sink } = rig({ fps: undefined })
    sink.push(KEY())
    sink.push(bytes(D_P))
    expect(decoded()).toEqual([['key', 0], ['delta', 33_333]])
  })

  it('skips to the next key picture while the decoder is behind', () => {
    const { sink } = rig()
    sink.push(KEY())
    decoder().decodeQueueSize = DECODE_QUEUE_MAX + 1
    sink.push(bytes(D_P, D_P))
    expect(decoded()).toEqual([['key', 0]])
    decoder().decodeQueueSize = 0
    sink.push(bytes(D_P))
    expect(decoded()).toEqual([['key', 0]])
    sink.push(KEY())
    expect(decoded().map(([type]) => type)).toEqual(['key', 'key'])
  })

  it('draws each picture and lets go of it, and draws nothing once closed', () => {
    const { sink, draw } = rig()
    sink.push(KEY())
    const first = { close: vi.fn() }
    decoder().init.output(first)
    expect(draw).toHaveBeenCalledWith(first)
    expect(first.close).toHaveBeenCalledOnce()
    sink.close()
    expect(decoder().state).toBe('closed')
    const late = { close: vi.fn() }
    decoder().init.output(late)
    expect(draw).toHaveBeenCalledOnce()
    expect(late.close).toHaveBeenCalledOnce()
    sink.push(KEY())
    sink.close()
    expect(FakeDecoder.all).toHaveLength(1)
  })

  it('lets go of a picture even when drawing it throws', () => {
    const { sink } = rig({ draw: vi.fn(() => { throw new Error('canvas gone') }) })
    sink.push(KEY())
    const frame = { close: vi.fn() }
    expect(() => decoder().init.output(frame)).toThrow('canvas gone')
    expect(frame.close).toHaveBeenCalledOnce()
  })

  it('says why and stops when the decoder cannot be configured or take a picture', () => {
    FakeDecoder.failConfigure = true
    const unsupported = rig()
    unsupported.sink.push(KEY())
    expect(unsupported.onError).toHaveBeenCalledWith(new Error('unsupported codec'))
    expect(decoder().state).toBe('closed')
    unsupported.sink.push(KEY())
    expect(FakeDecoder.all).toHaveLength(1)

    FakeDecoder.all = []
    FakeDecoder.failConfigure = false
    const broken = rig()
    broken.sink.push(KEY())
    decoder().failDecode = true
    broken.sink.push(bytes(D_P))
    expect(broken.onError).toHaveBeenCalledWith(new Error('bad picture'))
    expect(decoder().state).toBe('closed')
  })

  it('says why once when the decoder cannot be made or fails while decoding', () => {
    vi.stubGlobal('VideoDecoder', class { constructor() { throw new Error('no decoder') } })
    const none = rig()
    none.sink.push(KEY())
    expect(none.onError).toHaveBeenCalledWith(new Error('no decoder'))

    vi.stubGlobal('VideoDecoder', FakeDecoder)
    const failing = rig()
    failing.sink.push(KEY())
    decoder().init.error('boom')
    decoder().init.error(new Error('again'))
    expect(failing.onError).toHaveBeenCalledOnce()
    expect(failing.onError).toHaveBeenCalledWith(new Error('boom'))
  })
})

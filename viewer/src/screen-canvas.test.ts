// SPDX-License-Identifier: Apache-2.0
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createScreenCanvas, fitRect, framePoint, PAINT_QUEUE_MAX } from './screen-canvas'

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

class FakeImage {
  static all: FakeImage[] = []
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  naturalWidth = 402
  naturalHeight = 874
  src = ''
  constructor() { FakeImage.all.push(this) }
}

let drawImage: ReturnType<typeof vi.fn>

function canvasAt(box = { left: 0, top: 0, width: 402, height: 874 }) {
  const canvas = document.createElement('canvas')
  canvas.getBoundingClientRect = () => ({ ...box, right: box.left + box.width, bottom: box.top + box.height,
                                          x: box.left, y: box.top, toJSON: () => ({}) })
  return canvas
}

beforeEach(() => {
  FakeImage.all = []
  drawImage = vi.fn()
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage })) as never
  // A picture is drawn on the next animation frame; here that frame comes at once unless a test holds it.
  vi.stubGlobal('requestAnimationFrame', (paint: FrameRequestCallback) => { paint(0); return 1 })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('fitRect', () => {
  it('centres content whole in a box of another shape', () => {
    expect(fitRect({ w: 800, h: 874 }, { w: 402, h: 874 })).toEqual({ x: 199, y: 0, w: 402, h: 874 })
    expect(fitRect({ w: 402, h: 1000 }, { w: 402, h: 874 })).toEqual({ x: 0, y: 63, w: 402, h: 874 })
    expect(fitRect({ w: 201, h: 437 }, { w: 402, h: 874 })).toEqual({ x: 0, y: 0, w: 201, h: 437 })
  })

  it('fills the box when either has no size yet', () => {
    expect(fitRect({ w: 300, h: 200 }, { w: 0, h: 0 })).toEqual({ x: 0, y: 0, w: 300, h: 200 })
    expect(fitRect({ w: -5, h: 0 }, { w: 402, h: 874 })).toEqual({ x: 0, y: 0, w: 0, h: 0 })
  })
})

describe('framePoint', () => {
  const box = { left: 100, top: 50, width: 800, height: 874 }
  const phone = { w: 402, h: 874 }

  it('stretches over the whole box', () => {
    expect(framePoint({ clientX: 500, clientY: 487 }, box, { w: 1600, h: 1748 }, 'stretch'))
      .toEqual({ x: 800, y: 874, nx: 0.5, ny: 0.5, inside: true })
  })

  it('measures inside the letterboxed screen, and says a point in the bars is off it', () => {
    expect(framePoint({ clientX: 100 + 199 + 201, clientY: 50 + 437 }, box, phone, 'contain'))
      .toEqual({ x: 201, y: 437, nx: 0.5, ny: 0.5, inside: true })
    const bar = framePoint({ clientX: 120, clientY: 60 }, box, phone, 'contain')
    expect(bar).toEqual({ x: 0, y: 10, nx: 0, ny: 10 / 874, inside: false })
    const beyond = framePoint({ clientX: 2000, clientY: 2000 }, box, phone, 'contain')
    expect(beyond).toMatchObject({ x: 402, y: 874, nx: 1, ny: 1, inside: false })
  })

  it('maps one to one, and off the screen, in a box not laid out yet', () => {
    const none = { left: 0, top: 0, width: 0, height: 0 }
    expect(framePoint({ clientX: 5, clientY: 6 }, none, phone, 'stretch'))
      .toEqual({ x: 5, y: 6, nx: 0, ny: 0, inside: false })
    expect(framePoint({ clientX: -5, clientY: 6 }, none, phone, 'contain'))
      .toEqual({ x: 0, y: 6, nx: 0, ny: 0, inside: false })
  })
})

describe('createScreenCanvas with createImageBitmap', () => {
  function bitmaps() {
    const calls: Array<{ size: number; resolve: (b: object) => void; reject: (e: Error) => void }> = []
    vi.stubGlobal('createImageBitmap', vi.fn((blob: Blob) => new Promise((resolve, reject) => {
      calls.push({ size: blob.size, resolve, reject })
    })))
    const bitmap = (width = 402, height = 874) => ({ width, height, close: vi.fn() })
    return { calls, bitmap }
  }

  it('draws a frame at its pixels and measures it in the units it was sent with', async () => {
    const { calls, bitmap } = bitmaps()
    const canvas = canvasAt()
    const onFrame = vi.fn()
    const screen = createScreenCanvas(canvas, { onFrame })
    expect(screen.frame).toEqual({ w: 0, h: 0 })
    screen.drawBytes(new ArrayBuffer(3), { w: 402, h: 874 })
    const image = bitmap(1206, 2622)
    calls[0].resolve(image)
    await flush()
    expect([canvas.width, canvas.height]).toEqual([1206, 2622])
    expect(drawImage).toHaveBeenCalledWith(image, 0, 0)
    expect(image.close).toHaveBeenCalled()
    expect(screen.frame).toEqual({ w: 402, h: 874 })
    expect(onFrame).toHaveBeenCalledWith({ w: 402, h: 874 })
    expect(screen.point({ clientX: 201, clientY: 437 })).toMatchObject({ x: 201, y: 437, inside: true })
  })

  it('draws the newest frame after the one decoding, never the ones it replaced', async () => {
    const { calls, bitmap } = bitmaps()
    const screen = createScreenCanvas(canvasAt(), { fit: 'stretch' })
    screen.drawBytes(new ArrayBuffer(1))
    screen.drawBytes(new ArrayBuffer(2))
    screen.drawBytes(new ArrayBuffer(3))
    expect(calls.map((c) => c.size)).toEqual([1])
    calls[0].resolve(bitmap())
    await flush()
    expect(calls.map((c) => c.size)).toEqual([1, 3])
    calls[1].resolve(bitmap(10, 20))
    await flush()
    expect(drawImage).toHaveBeenCalledTimes(2)
    expect(screen.frame).toEqual({ w: 10, h: 20 })
  })

  it('skips a frame that does not decode and draws the next', async () => {
    const { calls, bitmap } = bitmaps()
    const screen = createScreenCanvas(canvasAt())
    screen.drawBytes(new ArrayBuffer(1))
    screen.drawBytes(new ArrayBuffer(2))
    calls[0].reject(new Error('corrupt'))
    await flush()
    calls[1].resolve(bitmap())
    await flush()
    expect(drawImage).toHaveBeenCalledOnce()
  })

  it('draws nothing more once destroyed, even a frame already decoding', async () => {
    const { calls, bitmap } = bitmaps()
    const screen = createScreenCanvas(canvasAt())
    screen.drawBytes(new ArrayBuffer(1))
    screen.drawBytes(new ArrayBuffer(2))
    screen.destroy()
    const image = bitmap()
    calls[0].resolve(image)
    await flush()
    expect(drawImage).not.toHaveBeenCalled()
    expect(image.close).toHaveBeenCalled()
    screen.drawBytes(new ArrayBuffer(3))
    expect(calls).toHaveLength(1)
  })
})

describe('createScreenCanvas without createImageBitmap', () => {
  beforeEach(() => {
    vi.stubGlobal('createImageBitmap', undefined)
    vi.stubGlobal('Image', FakeImage)
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:frame'), configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
  })

  it('decodes bytes through an image, and lets its address go whether it decoded or not', async () => {
    const screen = createScreenCanvas(canvasAt())
    // Before any frame, a canvas measures against its own pixels: 300 by 150 until something is drawn.
    const unset = createScreenCanvas(canvasAt({ left: 0, top: 0, width: 300, height: 150 }))
    expect(unset.point({ clientX: 150, clientY: 75 })).toMatchObject({ nx: 0.5, ny: 0.5, inside: true })
    screen.drawBytes(new ArrayBuffer(1))
    screen.drawBytes(new ArrayBuffer(2))
    expect(FakeImage.all[0].src).toBe('blob:frame')
    FakeImage.all[0].onerror?.()
    await flush()
    FakeImage.all[1].onload?.()
    await flush()
    expect(drawImage).toHaveBeenCalledWith(FakeImage.all[1], 0, 0)
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
    expect(screen.frame).toEqual({ w: 402, h: 874 })
  })
})

describe('createScreenCanvas with a decoded picture', () => {
  it('draws it at its pixels, measured in the units it was sent with, and nothing after destroy', () => {
    const canvas = canvasAt()
    const onFrame = vi.fn()
    const screen = createScreenCanvas(canvas, { onFrame })
    const picture = {} as CanvasImageSource
    screen.drawFrame(picture, 1206, 2622, { w: 402, h: 874 })
    expect(drawImage).toHaveBeenCalledWith(picture, 0, 0)
    expect([canvas.width, canvas.height]).toEqual([1206, 2622])
    expect(screen.frame).toEqual({ w: 402, h: 874 })
    screen.drawFrame(picture, 1206, 2622)
    expect(screen.frame).toEqual({ w: 1206, h: 2622 })
    expect(onFrame).toHaveBeenCalledTimes(2)
    screen.destroy()
    screen.drawFrame(picture, 10, 10)
    expect(drawImage).toHaveBeenCalledTimes(2)
  })
})

describe('pictures drawn one per animation frame', () => {
  function frames() {
    const waiting: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (paint: FrameRequestCallback) => { waiting.push(paint); return waiting.length })
    const cancel = vi.fn()
    vi.stubGlobal('cancelAnimationFrame', cancel)
    return { waiting, cancel, next: () => waiting.shift()?.(0) }
  }

  it('draws a burst on consecutive frames, and drops the oldest past the queue', () => {
    const clock = frames()
    const seen = { drawn: 0, dropped: 0 }
    const screen = createScreenCanvas(canvasAt(), { frames: { drawn: () => { seen.drawn += 1 }, dropped: () => { seen.dropped += 1 } } })
    const pictures = [1, 2, 3].map((n) => ({ n }) as unknown as CanvasImageSource)
    for (const picture of pictures) screen.drawFrame(picture, 402, 874)
    expect(PAINT_QUEUE_MAX).toBe(2)
    expect(seen.dropped).toBe(1)
    expect(drawImage).not.toHaveBeenCalled()
    clock.next()
    expect(drawImage).toHaveBeenLastCalledWith(pictures[1], 0, 0)
    clock.next()
    expect(drawImage).toHaveBeenLastCalledWith(pictures[2], 0, 0)
    expect(seen.drawn).toBe(2)
    expect(clock.waiting).toHaveLength(0)
  })

  it('keeps a VideoFrame of its own, closed once drawn or let go', () => {
    const clock = frames()
    const closed: number[] = []
    class FakeVideoFrame {
      constructor(readonly id: number) {}
      clone() { return new FakeVideoFrame(this.id + 100) }
      close() { closed.push(this.id) }
    }
    vi.stubGlobal('VideoFrame', FakeVideoFrame)
    const screen = createScreenCanvas(canvasAt())
    screen.drawFrame(new FakeVideoFrame(1) as unknown as CanvasImageSource, 402, 874)
    clock.next()
    expect(drawImage.mock.calls[0][0]).toMatchObject({ id: 101 })
    expect(closed).toEqual([101])
    screen.drawFrame(new FakeVideoFrame(2) as unknown as CanvasImageSource, 402, 874)
    screen.destroy()
    expect(clock.cancel).toHaveBeenCalled()
    expect(closed).toEqual([101, 102])
    screen.drawFrame(new FakeVideoFrame(3) as unknown as CanvasImageSource, 402, 874)
    expect(closed).toEqual([101, 102])
  })

  it('tells its observer what was received, decoded and replaced before decoding', async () => {
    const calls: string[] = []
    vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ width: 402, height: 874, close: vi.fn() })))
    const screen = createScreenCanvas(canvasAt(), { frames: {
      received: () => calls.push('in'), decoded: (ms) => calls.push(typeof ms === 'number' ? 'decoded' : 'decoded?'),
      drawn: () => calls.push('drawn'), dropped: () => calls.push('dropped'),
    } })
    screen.drawBytes(new ArrayBuffer(4))
    screen.drawBytes(new ArrayBuffer(4))
    screen.drawBytes(new ArrayBuffer(4))
    await flush()
    expect(calls).toEqual(['in', 'in', 'in', 'dropped', 'decoded', 'drawn', 'decoded', 'drawn'])
  })

  it('draws at once where there are no animation frames', () => {
    vi.stubGlobal('requestAnimationFrame', undefined)
    const screen = createScreenCanvas(canvasAt())
    const picture = {} as CanvasImageSource
    screen.drawFrame(picture, 402, 874)
    expect(drawImage).toHaveBeenCalledWith(picture, 0, 0)
  })
})

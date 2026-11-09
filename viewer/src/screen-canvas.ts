// SPDX-License-Identifier: Apache-2.0
/**
 * A device's screen drawn into a canvas, and a pointer on it turned back into that screen's own coordinates.
 *
 * Frames keep the device's shape inside the stage (`contain`, letterboxed like CSS `object-fit: contain`), so a point
 * in the bars around the screen is outside it. JPEG bytes are decoded off the main thread with `createImageBitmap`
 * where the browser has it, and through an `<img>` where it does not. The newest frame wins: frames that arrive while
 * one is decoding replace whichever was waiting, so a slow machine shows a later screen rather than falling behind.
 * An H.264 picture arrives already decoded (`h264-decoder.ts`).
 *
 * A decoded picture is drawn on the next animation frame, one per frame. Pictures come over a socket in bursts, and
 * two drawn within one display frame were one shown and one thrown away: a 60 fps stream showed 44-51 a second. Now
 * the second waits for the next frame, and only a third waiting drops the oldest (`PAINT_QUEUE_MAX`), so the screen
 * is never more than two frames behind.
 */
import type { Size } from './protocol.generated'

export type Fit = 'stretch' | 'contain'

/** Decoded pictures waiting for a display frame before the oldest is dropped. */
export const PAINT_QUEUE_MAX = 2

/**
 * Told what happens to each picture, for a host measuring how smoothly the screen draws: a piece received, a
 * picture decoded (with how long it took, where known), drawn, or thrown away before it was drawn. Every method is
 * optional, and none is called with anything of the picture itself.
 */
export interface FrameObserver {
  received?(): void
  decoded?(ms?: number): void
  drawn?(): void
  dropped?(count?: number): void
}

export interface Box {
  left: number
  top: number
  width: number
  height: number
}

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** Where a pointer is on the screen: in its units (`x`, `y`), as a share of it (`nx`, `ny`), and whether on it at all. */
export interface FramePoint {
  x: number
  y: number
  nx: number
  ny: number
  inside: boolean
}

/** Where content of one shape sits, centred and whole, in a box of another -- `object-fit: contain`. */
export function fitRect(box: Size, content: Size): Rect {
  if (box.w <= 0 || box.h <= 0 || content.w <= 0 || content.h <= 0) {
    return { x: 0, y: 0, w: Math.max(0, box.w), h: Math.max(0, box.h) }
  }
  const scale = Math.min(box.w / content.w, box.h / content.h)
  const w = content.w * scale
  const h = content.h * scale
  return { x: (box.w - w) / 2, y: (box.h - h) / 2, w, h }
}

const share = (value: number): number => Math.max(0, Math.min(1, value))

/** A pointer's place on a frame drawn into `box`, measured in the frame's own units. */
export function framePoint(client: { clientX: number; clientY: number }, box: Box, frame: Size, fit: Fit): FramePoint {
  const area = fit === 'contain' ? fitRect({ w: box.width, h: box.height }, frame)
    : { x: 0, y: 0, w: box.width, h: box.height }
  const rx = client.clientX - box.left - area.x
  const ry = client.clientY - box.top - area.y
  if (area.w <= 0 || area.h <= 0) {
    // Nothing laid out to measure against yet: the pointer's own offset, one to one.
    return { x: Math.max(0, rx), y: Math.max(0, ry), nx: 0, ny: 0, inside: false }
  }
  const inside = rx >= 0 && ry >= 0 && rx <= area.w && ry <= area.h
  const nx = share(rx / area.w)
  const ny = share(ry / area.h)
  return { x: nx * frame.w, y: ny * frame.h, nx, ny, inside }
}

export interface ScreenCanvasOptions {
  fit?: Fit
  /** Told about each picture; see `FrameObserver`. */
  frames?: FrameObserver
  /** Told the frame's size in its units after each frame is drawn. */
  onFrame?(frame: Size): void
}

export interface ScreenCanvas {
  /** The last frame's size in the screen's own units; the canvas's pixels until a frame says otherwise. */
  readonly frame: Size
  /** Draw a JPEG sent as bytes. `units` is its size in the screen's units, when not its pixels. */
  drawBytes(bytes: ArrayBuffer, units?: Size): void
  /**
   * Draw a picture already decoded -- an H.264 `VideoFrame` -- `width` by `height` pixels, on the next animation
   * frame. A `VideoFrame` is cloned, so the caller may close its own as soon as this returns.
   */
  drawFrame(source: CanvasImageSource, width: number, height: number, units?: Size): void
  point(event: { clientX: number; clientY: number }): FramePoint
  destroy(): void
}

interface Decoded {
  source: CanvasImageSource
  w: number
  h: number
  release(): void
}

function decode(bytes: ArrayBuffer): Promise<Decoded> {
  const blob = new Blob([bytes], { type: 'image/jpeg' })
  if (typeof createImageBitmap === 'function') {
    return createImageBitmap(blob).then((bitmap) => ({
      source: bitmap, w: bitmap.width, h: bitmap.height, release: () => bitmap.close(),
    }))
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const image = new Image()
    image.onload = () => {
      URL.revokeObjectURL(url)
      resolve({ source: image, w: image.naturalWidth, h: image.naturalHeight, release: () => {} })
    }
    image.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('a frame that does not decode'))
    }
    image.src = url
  })
}

interface Waiting extends Decoded {
  units?: Size
}

/** A `VideoFrame` of its own for a picture drawn later, so its caller may close theirs; anything else as it is. */
function hold(source: CanvasImageSource): { source: CanvasImageSource; release(): void } {
  const frame = source as { clone?: () => CanvasImageSource & { close(): void } }
  if (typeof VideoFrame !== 'undefined' && source instanceof VideoFrame && typeof frame.clone === 'function') {
    const own = frame.clone()
    return { source: own, release: () => own.close() }
  }
  return { source, release: () => {} }
}

export function createScreenCanvas(canvas: HTMLCanvasElement, options: ScreenCanvasOptions = {}): ScreenCanvas {
  const fit = options.fit ?? 'contain'
  const frames = options.frames
  let frame: Size = { w: 0, h: 0 }
  let destroyed = false
  let decoding = false
  let waiting: { bytes: ArrayBuffer; units?: Size } | null = null
  const shown: Waiting[] = []
  let painting: number | null = null

  function paint(source: CanvasImageSource, w: number, h: number, units?: Size): void {
    frame = { w: units?.w || w, h: units?.h || h }
    if (canvas.width !== w) canvas.width = w
    if (canvas.height !== h) canvas.height = h
    canvas.getContext('2d')?.drawImage(source, 0, 0)
    options.onFrame?.(frame)
  }

  function paintNext(): void {
    painting = null
    const next = shown.shift()
    if (!next) return
    try {
      if (!destroyed) {
        paint(next.source, next.w, next.h, next.units)
        frames?.drawn?.()
      }
    } finally {
      next.release()
    }
    if (shown.length) schedule()
  }

  function schedule(): void {
    if (painting !== null || destroyed) return
    // No animation frames here (a test, a worker): drawn at once, as before.
    if (typeof requestAnimationFrame !== 'function') {
      while (shown.length) paintNext()
      return
    }
    // Marked before it is asked for: a frame that runs at once (a host's own scheduler) leaves none pending.
    painting = -1
    const id = requestAnimationFrame(paintNext)
    if (painting === -1) painting = id
  }

  function present(picture: Waiting): void {
    if (destroyed) {
      picture.release()
      return
    }
    while (shown.length >= PAINT_QUEUE_MAX) {
      shown.shift()!.release()
      frames?.dropped?.()
    }
    shown.push(picture)
    schedule()
  }

  async function drain(): Promise<void> {
    decoding = true
    while (waiting && !destroyed) {
      const next = waiting
      waiting = null
      try {
        const started = performance.now()
        const image = await decode(next.bytes)
        frames?.decoded?.(performance.now() - started)
        present({ ...image, units: next.units })
      } catch {
        // Skipped: the next frame is a whole screen too.
      }
    }
    decoding = false
  }

  return {
    get frame() {
      return frame
    },
    drawBytes(bytes, units) {
      if (destroyed) return
      frames?.received?.()
      // The newest wins: one waiting to be decoded is replaced, and never drawn.
      if (waiting) frames?.dropped?.()
      waiting = { bytes, units }
      if (!decoding) void drain()
    },
    drawFrame(source, width, height, units) {
      if (destroyed) return
      present({ ...hold(source), w: width, h: height, units })
    },
    point(event) {
      const size = { w: frame.w || canvas.width, h: frame.h || canvas.height }
      return framePoint(event, canvas.getBoundingClientRect(), size, fit)
    },
    destroy() {
      destroyed = true
      waiting = null
      if (painting !== null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(painting)
      painting = null
      for (const picture of shown.splice(0)) picture.release()
    },
  }
}

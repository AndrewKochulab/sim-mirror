// SPDX-License-Identifier: Apache-2.0
/**
 * A device's screen drawn into a canvas, and a pointer on it turned back into that screen's own coordinates.
 *
 * Frames keep the device's shape inside the stage (`contain`, letterboxed like CSS `object-fit: contain`), so a point
 * in the bars around the screen is outside it. JPEG bytes are decoded off the main thread with `createImageBitmap`
 * where the browser has it, and through an `<img>` where it does not. The newest frame wins: frames that arrive while
 * one is decoding replace whichever was waiting, so a slow machine shows a later screen rather than falling behind.
 * An H.264 picture arrives already decoded (`h264-decoder.ts`) and is drawn as it is.
 */
import type { Size } from './protocol.generated'

export type Fit = 'stretch' | 'contain'

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
  /** Told the frame's size in its units after each frame is drawn. */
  onFrame?(frame: Size): void
}

export interface ScreenCanvas {
  /** The last frame's size in the screen's own units; the canvas's pixels until a frame says otherwise. */
  readonly frame: Size
  /** Draw a JPEG sent as bytes. `units` is its size in the screen's units, when not its pixels. */
  drawBytes(bytes: ArrayBuffer, units?: Size): void
  /** Draw a picture already decoded -- an H.264 `VideoFrame` -- `width` by `height` pixels. */
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

export function createScreenCanvas(canvas: HTMLCanvasElement, options: ScreenCanvasOptions = {}): ScreenCanvas {
  const fit = options.fit ?? 'contain'
  let frame: Size = { w: 0, h: 0 }
  let destroyed = false
  let decoding = false
  let waiting: { bytes: ArrayBuffer; units?: Size } | null = null

  function paint(source: CanvasImageSource, w: number, h: number, units?: Size): void {
    frame = { w: units?.w || w, h: units?.h || h }
    if (canvas.width !== w) canvas.width = w
    if (canvas.height !== h) canvas.height = h
    canvas.getContext('2d')?.drawImage(source, 0, 0)
    options.onFrame?.(frame)
  }

  async function drain(): Promise<void> {
    decoding = true
    while (waiting && !destroyed) {
      const next = waiting
      waiting = null
      try {
        const image = await decode(next.bytes)
        if (!destroyed) paint(image.source, image.w, image.h, next.units)
        image.release()
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
      waiting = { bytes, units }
      if (!decoding) void drain()
    },
    drawFrame(source, width, height, units) {
      if (!destroyed) paint(source, width, height, units)
    },
    point(event) {
      const size = { w: frame.w || canvas.width, h: frame.h || canvas.height }
      return framePoint(event, canvas.getBoundingClientRect(), size, fit)
    },
    destroy() {
      destroyed = true
      waiting = null
    },
  }
}

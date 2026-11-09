// SPDX-License-Identifier: Apache-2.0
/**
 * A device's H.264 frames, decoded by the browser into pictures a canvas can draw.
 *
 * `h264-stream.ts` cuts the stream into pictures; this hands them to WebCodecs. The decoder is configured from the
 * first sequence parameter set, and nothing is decoded before the first picture a decoder can start from. A decoder
 * that falls behind -- more than `DECODE_QUEUE_MAX` pictures waiting -- drops what it has not started until the next
 * key frame, so the screen shows now rather than a backlog.
 *
 * A decoder that cannot be made, cannot take a picture, or fails says so once and stops; the viewer falls back to JPEG.
 */
import { AccessUnitReader, codecString } from './h264-stream'
import type { FrameObserver } from './screen-canvas'

/** The most pictures left waiting for the decoder before it skips to the next key frame. */
export const DECODE_QUEUE_MAX = 4

export interface H264SinkOptions {
  /** Draw one decoded picture. It is closed afterwards; keep nothing of it. */
  draw(frame: VideoFrame): void
  /** The decoder gave up. Called once; the sink takes no more after it. */
  onError(error: Error): void
  /** Frames a second the stream runs at, for the pictures' timestamps. */
  fps?: number
  /** Told about each picture: received, decoded (and how long it took), or skipped to the next key frame. */
  frames?: FrameObserver
}

export interface H264Sink {
  push(bytes: Uint8Array): void
  close(): void
}

export function createH264Sink(options: H264SinkOptions): H264Sink {
  const reader = new AccessUnitReader()
  const step = Math.round(1_000_000 / (options.fps ?? 30))
  let decoder: VideoDecoder | null = null
  let waitingForKey = true
  let closed = false
  let timestamp = 0
  /** When each picture waiting in the decoder was handed to it, by its timestamp. */
  const handed = new Map<number, number>()
  const frames = options.frames

  function close(): void {
    closed = true
    if (decoder && decoder.state !== 'closed') decoder.close()
    decoder = null
  }

  function fail(err: unknown): void {
    if (closed) return
    close()
    options.onError(err instanceof Error ? err : new Error(String(err)))
  }

  function output(frame: VideoFrame): void {
    const at = handed.get(frame.timestamp)
    handed.delete(frame.timestamp)
    frames?.decoded?.(at === undefined ? undefined : performance.now() - at)
    try {
      if (!closed) options.draw(frame)
    } finally {
      frame.close()
    }
  }

  return {
    push(bytes) {
      if (closed) return
      for (const unit of reader.push(bytes)) {
        frames?.received?.()
        if (!decoder) {
          if (!unit.sps) continue
          try {
            decoder = new VideoDecoder({ output, error: fail })
            decoder.configure({ codec: codecString(unit.sps), optimizeForLatency: true })
          } catch (err) {
            fail(err)
            return
          }
        }
        if (decoder.decodeQueueSize > DECODE_QUEUE_MAX) waitingForKey = true
        if (waitingForKey && !unit.key) {
          frames?.dropped?.()
          continue
        }
        waitingForKey = false
        // A decoder that drops a picture never outputs it: what it was handed long ago is forgotten.
        if (handed.size > 64) handed.clear()
        handed.set(timestamp, performance.now())
        try {
          decoder.decode(new EncodedVideoChunk({ type: unit.key ? 'key' : 'delta', timestamp, data: unit.data }))
        } catch (err) {
          fail(err)
          return
        }
        timestamp += step
      }
    },
    close,
  }
}

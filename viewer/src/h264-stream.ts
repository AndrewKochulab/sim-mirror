// SPDX-License-Identifier: Apache-2.0
/**
 * An H.264 byte stream, as a screen socket relays it, cut into what a decoder takes.
 *
 * The stream is Annex-B: NAL units, each after a start code, arriving in chunks cut wherever the connector's pipe cut
 * them. WebCodecs decodes one access unit -- one picture -- at a time, so this finds the units across those cuts,
 * groups them into pictures, marks the ones a decoder can start from (an IDR, carried with its parameter sets), and
 * names the codec from the sequence parameter set.
 *
 * A picture is only known to be whole when the next one starts, so each is handed on one frame late: at 30 frames a
 * second, a thirtieth of a second.
 */

export const NAL_SLICE = 1
export const NAL_IDR = 5
export const NAL_SPS = 7

/** The most a stream may hold without a start code before it is taken to be garbage and dropped. */
export const BUFFER_MAX = 4 * 1024 * 1024

export interface AccessUnit {
  /** The picture's NAL units, each with its start code: what an `EncodedVideoChunk` takes. */
  data: Uint8Array
  /** Whether a decoder can start here: the picture is an IDR. */
  key: boolean
  /** Its sequence parameter set, without the start code, when it carries one. */
  sps: Uint8Array | null
}

/** Each start code in `bytes`: where it begins and how long it is, 3 or 4 bytes. */
function startCodes(bytes: Uint8Array): Array<[number, number]> {
  const found: Array<[number, number]> = []
  for (let i = 0; i + 2 < bytes.length; i++) {
    if (bytes[i] !== 0 || bytes[i + 1] !== 0 || bytes[i + 2] !== 1) continue
    const long = i > 0 && bytes[i - 1] === 0
    found.push(long ? [i - 1, 4] : [i, 3])
    i += 2
  }
  return found
}

function joined(parts: readonly Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0))
  let at = 0
  for (const part of parts) {
    out.set(part, at)
    at += part.length
  }
  return out
}

interface Nal {
  bytes: Uint8Array
  start: number
  type: number
}

/** Reads a stream chunk by chunk, answering with the pictures each chunk completes. */
export class AccessUnitReader {
  private pending: Uint8Array = new Uint8Array(0)
  private picture: Nal[] = []
  private hasSlice = false

  push(chunk: Uint8Array): AccessUnit[] {
    const bytes = joined([this.pending, chunk])
    const codes = startCodes(bytes)
    const done: AccessUnit[] = []
    if (codes.length === 0) {
      this.pending = bytes.length > BUFFER_MAX ? new Uint8Array(0) : bytes
      return done
    }
    // A unit runs from its start code to the next; the last is not known to be whole yet.
    for (let k = 0; k + 1 < codes.length; k++) {
      const [at, length] = codes[k]
      const unit = bytes.subarray(at, codes[k + 1][0])
      const finished = this.take({ bytes: unit, start: length, type: unit[length] & 0x1f })
      if (finished) done.push(finished)
    }
    this.pending = bytes.slice(codes[codes.length - 1][0])
    return done
  }

  /** Add one unit to the picture being gathered, answering with the picture it ends, if it ends one. */
  private take(nal: Nal): AccessUnit | null {
    const slice = nal.type === NAL_SLICE || nal.type === NAL_IDR
    // A slice whose first macroblock is 0 starts a picture, and so does anything but a slice after one.
    const firstSlice = slice && ((nal.bytes[nal.start + 1] ?? 0) & 0x80) !== 0
    const finished = this.hasSlice && (!slice || firstSlice) ? this.finish() : null
    this.picture.push(nal)
    if (slice) this.hasSlice = true
    return finished
  }

  private finish(): AccessUnit {
    const units = this.picture
    const sps = units.find((nal) => nal.type === NAL_SPS)
    this.picture = []
    this.hasSlice = false
    return {
      data: joined(units.map((nal) => nal.bytes)),
      key: units.some((nal) => nal.type === NAL_IDR),
      sps: sps ? sps.bytes.subarray(sps.start) : null,
    }
  }
}

const hex = (byte: number): string => byte.toString(16).padStart(2, '0')

/** The WebCodecs codec string for a sequence parameter set: `avc1.` then its profile, constraints and level. */
export function codecString(sps: Uint8Array): string {
  return `avc1.${hex(sps[1] ?? 0)}${hex(sps[2] ?? 0)}${hex(sps[3] ?? 0)}`
}

/** Whether this page can decode H.264 itself: WebCodecs, which browsers give only to a secure page. */
export function canDecodeH264(
  scope: { VideoDecoder?: unknown; EncodedVideoChunk?: unknown; isSecureContext?: boolean } = globalThis,
): boolean {
  return typeof scope.VideoDecoder === 'function' && typeof scope.EncodedVideoChunk === 'function'
    && scope.isSecureContext === true
}

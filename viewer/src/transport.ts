// SPDX-License-Identifier: Apache-2.0
/**
 * How the viewer reaches its server: a scope's HTTP routes, and its screen socket's URL.
 *
 * `createHttpTransport` talks to a SimMirror daemon or any host that mounts SimMirror's routers. A host with its own
 * API client -- its own authentication, its own error handling -- passes an object of this shape instead.
 */
import type { DeviceChoice, ScopeStatus, Started } from './protocol.generated'

export interface SimMirrorTransport {
  /** Whether the scope can have a simulator, why not, and how its device stands. */
  status(): Promise<ScopeStatus>
  /** Bring the device up if needed, and mint a one-shot ticket for its screen. Rejects with a message worth showing. */
  start(): Promise<Started>
  /** Let the device go; with `shutdown`, shut it down too. Answers whether one was running. */
  stop(shutdown: boolean): Promise<boolean>
  /** This Mac's simulators the scope could use. */
  devices(): Promise<DeviceChoice[]>
  /** Use this simulator for the scope from now on. */
  choose(udid: string): Promise<void>
  /** The screen socket's URL for a ticket. */
  socketUrl(ticket: string): string
}

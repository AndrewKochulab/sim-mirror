// SPDX-License-Identifier: Apache-2.0
/**
 * What a device is, as the viewer says it: a simulator, an Android emulator, or a real device -- an iPhone or iPad, or
 * an Android phone. A real device is never started or shut down by SimMirror, only shown and let go; a simulator and an
 * emulator run on this Mac, and are.
 */
import type { Connection, Device, DeviceChoice, PanelButton } from './protocol.generated'

type Described = Pick<Device | DeviceChoice, 'kind' | 'connection'>

/** The buttons a device can press when its server does not say: Home and Lock, as the toolbar always had. */
export const DEFAULT_BUTTONS: readonly PanelButton[] = ['home', 'lock']

/** Whether a device is a real one, in a person's hand: an iPhone or iPad, or an Android phone. */
export function isReal(device: Described | null | undefined): boolean {
  return device?.kind === 'physical' || (device?.kind === 'android' && device.connection !== null)
}

/** How the viewer calls a device in a sentence: "the simulator", "the emulator", "the device". */
export function deviceNoun(device: Described | null | undefined): 'simulator' | 'emulator' | 'device' {
  if (isReal(device)) return 'device'
  return device?.kind === 'android' ? 'emulator' : 'simulator'
}

/** How a real device reaches this Mac, as a person reads it. */
export function connectionLabel(connection: Connection | null): string {
  return connection === 'usb' ? 'USB' : connection ? 'Wi-Fi' : 'Not connected'
}

/** The chip beside a device's name: what it is, when it is not a simulator, and how it is connected. */
export function kindChip(device: Described | null | undefined): string {
  if (device?.kind === 'physical') return `Real device · ${connectionLabel(device.connection)}`
  if (device?.kind === 'android') return `Android · ${device.connection ? connectionLabel(device.connection) : 'Emulator'}`
  return ''
}

/** The buttons a device can press now: what its server says, else Home and Lock. */
export function buttonsOf(device: Device | null | undefined): readonly PanelButton[] {
  return device?.buttons ?? DEFAULT_BUTTONS
}

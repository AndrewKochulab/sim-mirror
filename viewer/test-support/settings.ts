// SPDX-License-Identifier: Apache-2.0
/** A scope's settings as the server answers them, trimmed to a setting of each kind, for the panel's tests. */
import type { SettingEntry, SettingsView } from '../src/protocol.generated'

export function entry(path: string, overrides: Partial<SettingEntry> = {}): SettingEntry {
  return {
    path,
    section: path.includes('.') ? path.split('.')[0] : '',
    doc: `What ${path} does.`,
    rule: { kind: 'flag' },
    value: true,
    default: true,
    origin: { layer: 'default', detail: null },
    effect: 'live',
    reach: 'scope',
    sensitive: false,
    locked: null,
    command: `sim-mirror config set ${path} <value> --scope demo`,
    ...overrides,
  }
}

export function settingsView(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    scope: 'demo',
    access: 'write',
    notice: null,
    sections: [
      { id: '', title: 'General', doc: 'Whether devices are brought up at all.' },
      { id: 'stream', title: 'Stream', doc: 'How the screen is sent to a viewer.' },
      { id: 'server', title: 'Server', doc: 'Where the daemon listens.' },
    ],
    settings: [
      entry('enabled'),
      entry('stream.fps', { rule: { kind: 'whole', low: 5, high: 60 }, value: 30, default: 30,
        origin: { layer: 'scope', detail: '[scopes."demo"]' } }),
      entry('stream.encoding', { rule: { kind: 'choice', options: ['auto', 'jpeg', 'h264'] }, value: 'auto',
        default: 'auto', effect: 'next_connection', origin: { layer: 'file', detail: 'config.toml' } }),
      entry('stream.quality', { rule: { kind: 'whole', low: 30, high: 100 }, value: 60, default: 75,
        origin: { layer: 'environment', detail: 'SIM_MIRROR_STREAM_QUALITY' },
        locked: "Set by SIM_MIRROR_STREAM_QUALITY in the daemon's environment, which config.toml cannot override: change it there." }),
      entry('server.port', { rule: { kind: 'whole', low: 1024, high: 65535 }, value: 7466, default: 7466,
        reach: 'global', effect: 'restart', sensitive: true, command: 'sim-mirror config set server.port <value>' }),
    ],
    ...overrides,
  }
}

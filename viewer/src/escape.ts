// SPDX-License-Identifier: Apache-2.0
/** Text made safe inside HTML: the characters markup is made of, as entities. */
export function escapeHTML(value: string | null | undefined): string {
  if (!value) return ''
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// SPDX-License-Identifier: Apache-2.0
// Inject only from a Debug host into an explicitly registered webview. No socket, secret or OS input lives here.
(() => {
  if (Object.prototype.hasOwnProperty.call(globalThis, "__SimMirrorWeb")) return;
  const origin = location.origin;
  let field = null;
  const refuse = (message) => { throw new Error(message); };
  const finite = (value) => typeof value === 'number' && Number.isFinite(value);
  const text = (value) => String(value || '').trim().slice(0, 2000);
  const kind = (el) => {
    const role = el.getAttribute('role');
    const roles = { button: 'button', link: 'link', checkbox: 'switch', radio: 'switch', textbox: 'field', heading: 'heading', list: 'list', listbox: 'list', slider: 'slider', combobox: 'picker' };
    if (el instanceof HTMLInputElement && el.type === 'password') return 'secure';
    if (roles[role]) return roles[role];
    if (el instanceof HTMLButtonElement) return 'button';
    if (el instanceof HTMLAnchorElement) return 'link';
    if (el instanceof HTMLSelectElement) return 'picker';
    if (el instanceof HTMLTextAreaElement || el.isContentEditable) return 'field';
    if (el instanceof HTMLInputElement) return ['checkbox', 'radio'].includes(el.type) ? 'switch' : 'field';
    if (/^H[1-6]$/.test(el.tagName)) return 'heading';
    if (['UL', 'OL'].includes(el.tagName)) return 'list';
    return el.children.length ? 'container' : 'text';
  };
  const check = () => { if (location.origin !== origin) refuse('webview navigation changed its origin'); };
  const target = (command, offset) => {
    const x = command.x - offset.x, y = command.y - offset.y;
    if (!finite(x) || !finite(y) || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) refuse('point is outside the registered webview');
    const el = document.elementFromPoint(x, y);
    if (!(el instanceof HTMLElement) || el instanceof HTMLIFrameElement) refuse('no app element at this point');
    return { el, x, y };
  };
  const events = (el, type, x, y, button = 0) => el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button }));
  function capture(options) {
    check();
    let remaining = Math.min(3000, options.maxNodes), count = 0, truncated = false;
    const walk = (el, depth, clip) => {
      if (!(el instanceof HTMLElement) || depth > 40 || ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName)) return [];
      if (remaining-- <= 0) { truncated = true; return []; }
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' || el.hidden) return [];
      const visible = { left: Math.max(rect.left, clip.left), top: Math.max(rect.top, clip.top), right: Math.min(rect.right, clip.right), bottom: Math.min(rect.bottom, clip.bottom) };
      const clips = ['auto', 'scroll', 'hidden', 'clip'];
      const childClip = { left: clips.includes(style.overflowX || style.overflow) ? visible.left : clip.left, right: clips.includes(style.overflowX || style.overflow) ? visible.right : clip.right,
        top: clips.includes(style.overflowY || style.overflow) ? visible.top : clip.top, bottom: clips.includes(style.overflowY || style.overflow) ? visible.bottom : clip.bottom };
      const children = Array.from(el.children).flatMap(child => walk(child, depth + 1, childClip));
      if (visible.right <= visible.left || visible.bottom <= visible.top) return children;
      const type = kind(el), secure = type === 'secure';
      const label = text(el.getAttribute('aria-label') || (el.labels && Array.from(el.labels).map(l => l.textContent).join(' ')) || el.getAttribute('title') || (!['field', 'secure', 'container', 'list'].includes(type) ? el.textContent : '')) || null;
      count++;
      return [{ kind: type, label, label_source: label ? 'accessibility' : null, identifier: el.id || null,
        value: secure ? null : ('checked' in el && ['checkbox', 'radio'].includes(el.type) ? String(el.checked) : 'value' in el ? text(el.value) : null),
        placeholder: el.getAttribute('placeholder'), frame: { x: visible.left + options.offset.x, y: visible.top + options.offset.y, width: visible.right - visible.left, height: visible.bottom - visible.top },
        traits: [], enabled: !el.matches(':disabled') && el.getAttribute('aria-disabled') !== 'true', interactive: ['button', 'link', 'field', 'secure', 'switch', 'slider', 'picker'].includes(type), source: 'web', type_name: el.tagName, children: secure ? [] : children }];
    };
    const nodes = walk(document.body, 0, { left: 0, top: 0, right: innerWidth, bottom: innerHeight });
    return { protocol: 1, sdk_version: '0.1.0', app: options.app, screen: options.screen, modal: null, keyboard: null,
      windows: [{ level: 0, key: false, nodes }], truncated, node_count: count, capture_ms: 0, notes: ['Debug web bridge; cross-origin frames and trusted native events are unavailable.'] };
  }
  function input(command, offset) {
    check();
    if (['tap', 'right_click', 'hover', 'scroll'].includes(command.kind)) {
      const { el, x, y } = target(command, offset);
      if (el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') refuse('element is disabled');
      if (command.kind === 'tap') {
        if (['field', 'secure'].includes(kind(el))) { field = el; el.focus({ preventScroll: true }); }
        el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', buttons: 1 }));
        events(el, 'mousedown', x, y);
        el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse' }));
        events(el, 'mouseup', x, y);
        el.click();
      } else if (command.kind === 'right_click') {
        if (events(el, 'contextmenu', x, y, 2)) refuse('context-menu event delivered without an app handler; native menu tracking is unavailable');
      }
      else if (command.kind === 'hover') { events(el, 'mouseover', x, y); events(el, 'mousemove', x, y); }
      else {
        if (!finite(command.dx) || !finite(command.dy) || Math.abs(command.dx) > 10000 || Math.abs(command.dy) > 10000) refuse('invalid scroll delta');
        let scroll = el;
        while (scroll.parentElement && !(scroll.scrollHeight > scroll.clientHeight || scroll.scrollWidth > scroll.clientWidth)) scroll = scroll.parentElement;
        scroll.scrollBy({ left: command.dx, top: command.dy, behavior: 'instant' });
      }
    } else if (command.kind === 'type') {
      if (!field || !field.isConnected || field.ownerDocument !== document) refuse('choose a text field first');
      if (typeof command.text !== 'string' || !command.text.length || command.text.length > 2000 || command.text.includes('\0')) refuse('invalid text');
      if (command.submit) refuse('submit is explicit; text was not changed');
      if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
        if (field.readOnly || field.disabled) refuse('field is read-only');
        const descriptor = Object.getOwnPropertyDescriptor(field instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype, 'value');
        descriptor.set.call(field, command.clear ? command.text : field.value + command.text);
        field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: command.text }));
        field.dispatchEvent(new Event('change', { bubbles: true }));
      } else { refuse('contenteditable typing requires an app hook'); }
    } else if (command.kind === 'keys') {
      const keys = { return: 'Enter', escape: 'Escape', tab: 'Tab', 'shift+tab': 'Tab', left: 'ArrowLeft', right: 'ArrowRight', up: 'ArrowUp', down: 'ArrowDown', space: ' ' };
      if (typeof command.text !== 'string' || !(command.text in keys)) refuse('this chord requires a native host hook');
      const el = document.activeElement instanceof HTMLElement ? document.activeElement : document.body;
      const handled = !el.dispatchEvent(new KeyboardEvent('keydown', { key: keys[command.text], shiftKey: command.text === 'shift+tab', bubbles: true, cancelable: true }));
      el.dispatchEvent(new KeyboardEvent('keyup', { key: keys[command.text], bubbles: true }));
      if (!handled) {
        if (command.text === 'tab' || command.text === 'shift+tab') {
          const candidates = Array.from(document.querySelectorAll('button, input, textarea, select, a[href], [tabindex]')).filter(node =>
            node instanceof HTMLElement && node.tabIndex >= 0 && !node.matches(':disabled') && !node.closest('[inert]') &&
            getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0);
          if (!candidates.length) refuse('this document has no focusable control');
          const index = candidates.indexOf(el), step = command.text === 'tab' ? 1 : -1;
          const next = candidates[(index + step + candidates.length) % candidates.length];
          next.focus({ preventScroll: true });
          next.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          field = ['field', 'secure'].includes(kind(next)) ? next : null;
        } else if ((command.text === 'return' || command.text === 'space') && el instanceof HTMLButtonElement) {
          el.click();
        } else if (['left', 'right'].includes(command.text) && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && el.selectionStart !== null) {
          const position = command.text === 'left' ? Math.max(0, el.selectionStart - 1) : Math.min(el.value.length, el.selectionEnd + 1);
          el.setSelectionRange(position, position);
        } else {
          refuse('key event delivered; no app handler consumed it and its browser default is unavailable; inspect the state');
        }
      }
    } else { refuse('operation requires a native host hook'); }
    return {};
  }
  Object.defineProperty(globalThis, '__SimMirrorWeb', { value: Object.freeze({ capture, input }), configurable: true });
})();

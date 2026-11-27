() => {
  // What a phone must never show, each finding naming its element: a band short of its card's
  // edge, text its box cuts off, a field too narrow for its placeholder, a control's label broken
  // over two lines, text drawn over text, a mark drawn over text or over a field's text area, and
  // a page wider than the screen. Run by test_phone_layout_browser.py.
  const found = [];
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    if (el.classList.length) s += '.' + [...el.classList].slice(0, 2).join('.');
    return s;
  };
  const css = (el) => getComputedStyle(el);
  const clear = (colour) => /rgba\(0, 0, 0, 0\)|transparent/.test(colour);
  // A positioned, opaque layer: a dropdown's panel, a sticky header. Something laid over text by
  // accident is a defect even when it hides the text completely.
  const overlay = (el) => {
    const c = css(el).backgroundColor.match(/[\d.]+/g);
    return !!c && (c.length < 4 || parseFloat(c[3]) >= 0.98) && css(el).position !== 'static';
  };

  // Out of sight on purpose: a closed fold or dialog, screen-reader text, the skip link before
  // focus. Chromium still reports positions for some of these.
  const folded = (el) => {
    for (let d = el.closest('details:not([open])'); d; d = d.parentElement?.closest('details:not([open])')) {
      const summary = [...d.children].find((c) => c.tagName === 'SUMMARY');
      if (!(summary && summary.contains(el))) return true;
    }
    const dialog = el.closest('dialog');
    return !!(dialog && !dialog.open);
  };
  const unseen = (el) => {
    if (folded(el)) return true;
    for (let e = el; e; e = e.parentElement) {
      const s = css(e);
      if (e.hidden || e.classList.contains('sr-only') || e.classList.contains('skip')) return true;
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return true;
      if ((s.clip !== 'auto' || s.clipPath !== 'none') && s.position === 'absolute') return true;
      const r = e.getBoundingClientRect();
      if (r.width <= 2 || r.height <= 2) return true;
    }
    return false;
  };
  // The part of a box its clipping ancestors leave on screen, or null. With hiddenOnly, a box
  // that scrolls does not count as clipping, since what it hides is a swipe away.
  const visible = (r, el, hiddenOnly) => {
    let [left, top, right, bottom] = [r.left, r.top, r.right, r.bottom];
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const s = css(e);
      const clips = hiddenOnly
        ? /hidden|clip/.test(s.overflowX + s.overflowY)
        : s.overflowX !== 'visible' || s.overflowY !== 'visible';
      if (!clips) continue;
      const c = e.getBoundingClientRect();
      [left, top, right, bottom] = [Math.max(left, c.left), Math.max(top, c.top), Math.min(right, c.right), Math.min(bottom, c.bottom)];
    }
    return right > left && bottom > top ? {left, top, right, bottom, width: right - left, height: bottom - top} : null;
  };
  const clamped = (el) => {
    for (let e = el; e; e = e.parentElement) if (css(e).webkitLineClamp !== 'none') return true;
    return false;
  };
  // A sticky header or a bottom bar covers the page as it scrolls by design.
  const floating = (el) => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (['sticky', 'fixed'].includes(css(e).position)) return true;
    }
    return false;
  };
  // Of two overlapping things, true when the one on top hides the other behind an overlay.
  const covered = (a, b, x, y) => {
    if (floating(a) !== floating(b)) return true;
    const top = document.elementFromPoint(x, y);
    if (!top) return false;
    const holds = (el) => top === el || el.contains(top) || top.contains(el);
    const below = holds(a) ? b : holds(b) ? a : null;
    if (!below) {
      // Something else is on top: an overlay of its own hides both.
      for (let e = top; e && !e.contains(a) && !e.contains(b); e = e.parentElement) if (overlay(e)) return true;
      return false;
    }
    // One of the two is on top: it hides the other when an overlay lies between them.
    for (let e = below === a ? b : a; e && !e.contains(below); e = e.parentElement) if (overlay(e)) return true;
    return false;
  };
  const meet = (p, q) => {
    const w = Math.min(p.right, q.right) - Math.max(p.left, q.left);
    const h = Math.min(p.bottom, q.bottom) - Math.max(p.top, q.top);
    return {w, h, x: (Math.max(p.left, q.left) + Math.min(p.right, q.right)) / 2, y: (Math.max(p.top, q.top) + Math.min(p.bottom, q.bottom)) / 2};
  };

  // Every line of text on screen, as its box.
  const lines = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement;
    if (!n.textContent.trim() || !el || unseen(el)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const raw of range.getClientRects()) {
      const r = visible(raw, el, false);
      if (r && r.width > 2 && r.height > 2) lines.push({r, el, text: n.textContent.trim().slice(0, 24)});
    }
  }

  for (const el of document.querySelectorAll('body *')) {
    const s = css(el);
    // A band meant to bleed to its card's edge: a coloured box pulled out by negative margins.
    const ml = parseFloat(s.marginLeft), mr = parseFloat(s.marginRight);
    if ((ml < 0 || mr < 0) && s.display !== 'none' && !['absolute', 'fixed'].includes(s.position) &&
        (s.backgroundImage !== 'none' || !clear(s.backgroundColor)) && el.parentElement) {
      const r = el.getBoundingClientRect(), pr = el.parentElement.getBoundingClientRect(), ps = css(el.parentElement);
      const inner = {left: pr.left + parseFloat(ps.borderLeftWidth), right: pr.right - parseFloat(ps.borderRightWidth)};
      const clips = ps.overflowX !== 'visible';
      const short = (gap) => gap > 1 || (gap < -1 && !clips);
      if (r.width && ml < 0 && short(r.left - inner.left)) found.push(`band short of its card's left edge: ${name(el)}`);
      if (r.width && mr < 0 && short(inner.right - r.right)) found.push(`band short of its card's right edge: ${name(el)}`);
    }
    if (unseen(el)) continue;
    // Text its box cuts off; a deliberate «…» clamp and decorative glyphs are left alone.
    if (!el.closest('[aria-hidden="true"]') && !clamped(el)) {
      for (const n of el.childNodes) {
        if (n.nodeType !== 3 || !n.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        const cut = [...range.getClientRects()].some((raw) => {
          if (raw.width < 2) return false;
          const v = visible(raw, el, true);
          return !v || v.width < raw.width - 1 || v.height < raw.height - 2;
        });
        if (cut) { found.push(`text cut off: ${name(el)} «${n.textContent.trim().slice(0, 24)}»`); break; }
      }
    }
    // A label inside a button, pill or tab broken over two lines. Inline links in a sentence wrap
    // with it, so only controls that are boxes of their own count.
    if (el.matches('a, button, summary, .badge') && s.display !== 'inline' &&
        (!clear(s.backgroundColor) || s.backgroundImage !== 'none' ||
         ['Top', 'Right', 'Bottom', 'Left'].some((k) => parseFloat(s[`border${k}Width`]) > 0))) {
      const texts = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = texts.nextNode(); n; n = texts.nextNode()) {
        if (!n.textContent.trim() || unseen(n.parentElement)) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        const rows = new Set([...range.getClientRects()].filter((r) => r.width > 2).map((r) => Math.round(r.top / 6)));
        if (rows.size > 1) { found.push(`label broken over two lines: ${name(el)} «${n.textContent.trim().slice(0, 24)}»`); break; }
      }
    }
  }

  // A field narrower than its placeholder or its value.
  const measure = document.createElement('canvas').getContext('2d');
  for (const field of document.querySelectorAll('input')) {
    if (unseen(field) || ['hidden', 'radio', 'checkbox', 'submit', 'button'].includes(field.type)) continue;
    const s = css(field);
    measure.font = `${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
    const room = field.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    for (const text of [field.placeholder, field.value]) {
      if (text && measure.measureText(text).width > room + 1) found.push(`field too narrow for «${text.slice(0, 24)}»: ${name(field)}`);
    }
  }

  // Text drawn over text.
  const seen = new Set();
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const a = lines[i], b = lines[j];
      if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const m = meet(a.r, b.r);
      if (m.w > 3 && m.h > 4 && !covered(a.el, b.el, m.x, m.y)) {
        const key = `text over text: ${name(a.el)} «${a.text}» and ${name(b.el)} «${b.text}»`;
        if (!seen.has(key)) { seen.add(key); found.push(key); }
      }
    }
  }

  // A mark or icon over text or over a field's text area. A drawing 200px or more on both sides
  // is a background for its whole section, not a mark placed on something.
  for (const svg of document.querySelectorAll('svg')) {
    const full = svg.getBoundingClientRect();
    if (full.width >= 200 && full.height >= 200) continue;
    const r = visible(full, svg, false);
    if (!r || r.width < 4 || unseen(svg)) continue;
    for (const field of document.querySelectorAll('input, textarea')) {
      if (unseen(field) || ['hidden', 'radio', 'checkbox'].includes(field.type)) continue;
      const fr = field.getBoundingClientRect(), s = css(field);
      const area = {left: fr.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft), right: fr.right - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight), top: fr.top, bottom: fr.bottom};
      const m = meet(r, area);
      if (m.w > 1 && m.h > 4) found.push(`icon over the text of ${name(field)}`);
    }
    for (const line of lines) {
      if (svg.contains(line.el) || line.el.contains(svg)) continue;
      const m = meet(r, line.r);
      if (m.w > 2 && m.h > 4 && !covered(svg, line.el, m.x, m.y)) found.push(`icon over text: ${name(svg.parentElement)} on «${line.text}»`);
    }
  }

  if (document.documentElement.scrollWidth > innerWidth + 1) {
    found.push(`page wider than the screen: ${document.documentElement.scrollWidth}px`);
  }
  return [...new Set(found)];
}

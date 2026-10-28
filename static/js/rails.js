(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const header = document.querySelector('.v-header');
  if (header) new ResizeObserver(() => {
    const height = Math.ceil(header.getBoundingClientRect().height);
    document.documentElement.style.setProperty('--hdr-h', `${height}px`);
    document.documentElement.style.scrollPaddingTop = `${height + 18}px`;
  }).observe(header);

  document.querySelectorAll('.v-mobile-cats').forEach(menu => {
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape' && menu.open) {
        menu.open = false;
        menu.querySelector('summary').focus();
        event.preventDefault();
      }
    });
    document.addEventListener('pointerdown', event => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    menu.querySelectorAll('a').forEach(link => link.addEventListener('click', () => { menu.open = false; }));
  });

  document.querySelectorAll('[data-rail]').forEach(root => {
    const track = root.querySelector('[data-rail-track]');
    const controls = root.querySelector('.v-rail-controls');
    const previous = root.querySelector('[data-rail-prev]');
    const next = root.querySelector('[data-rail-next]');
    const meter = root.querySelector('[data-rail-meter]');
    const cards = [...track.querySelectorAll('.pcard')];
    const rtl = getComputedStyle(track).direction === 'rtl';
    let drag = null, suppressClick = false;
    const position = () => Math.max(0, rtl ? -track.scrollLeft : track.scrollLeft);
    const maximum = () => Math.max(0, track.scrollWidth - track.clientWidth);
    const moveTo = (value, instant = false) => {
      track.scrollTo({left:(rtl ? -1 : 1) * value, behavior:instant || reduced.matches ? 'instant' : 'smooth'});
    };
    const step = direction => {
      const box = track.getBoundingClientRect(), offset = position();
      const points = cards.map(card => {
        const rect = card.getBoundingClientRect();
        return Math.min(maximum(), Math.max(0, offset + (rtl ? box.right - rect.right : rect.left - box.left)));
      });
      const here = position();
      const target = direction > 0 ? points.find(value => value > here + 3) : points.reverse().find(value => value < here - 3);
      moveTo(target === undefined ? (direction > 0 ? maximum() : 0) : target);
    };
    const sync = () => {
      const overflows = maximum() > 3;
      controls.hidden = !overflows;
      track.tabIndex = overflows ? 0 : -1;
      previous.disabled = position() < 3;
      next.disabled = position() >= maximum() - 3;
      track.classList.toggle('is-draggable', overflows);
      if (meter) {
        meter.hidden = !overflows;
        meter.style.setProperty('--rail-progress', String(Math.min(1, (position() + track.clientWidth) / Math.max(1, track.scrollWidth))));
      }
    };
    previous.addEventListener('click', () => step(-1));
    next.addEventListener('click', () => step(1));
    track.addEventListener('scroll', sync, {passive:true});
    track.addEventListener('keydown', event => {
      if (event.target !== track) return;
      const forward = rtl ? 'ArrowLeft' : 'ArrowRight';
      const backward = rtl ? 'ArrowRight' : 'ArrowLeft';
      if (![forward, backward, 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home') moveTo(0);
      else if (event.key === 'End') moveTo(maximum());
      else step(event.key === forward ? 1 : -1);
    });
    track.addEventListener('dragstart', event => event.preventDefault());
    track.addEventListener('pointerdown', event => {
      suppressClick = false;
      if (event.pointerType === 'mouse' && event.button === 0 && maximum() >= 3) {
        drag = {id:event.pointerId, x:event.clientX, scroll:track.scrollLeft, moved:false};
      }
    });
    track.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      if (!(event.buttons & 1)) { endDrag(event); return; }
      const delta = event.clientX - drag.x;
      if (!drag.moved && Math.abs(delta) <= 6) return;
      drag.moved = true;
      track.setPointerCapture(event.pointerId);
      track.classList.add('is-dragging');
      event.preventDefault();
      track.scrollLeft = drag.scroll - delta;
    });
    const endDrag = event => {
      if (!drag || drag.id !== event.pointerId) return;
      suppressClick = drag.moved;
      drag = null;
      track.classList.remove('is-dragging');
      setTimeout(() => { suppressClick = false; }, 250);
      sync();
    };
    for (const event of ['pointerup','pointercancel','lostpointercapture']) track.addEventListener(event, endDrag);
    track.addEventListener('click', event => {
      if (suppressClick) { event.preventDefault(); event.stopPropagation(); }
    }, true);
    new ResizeObserver(sync).observe(track);
    document.fonts.ready.then(sync);
    sync();
  });
})();

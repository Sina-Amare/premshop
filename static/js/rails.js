(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = matchMedia('(min-width: 761px) and (hover: hover) and (pointer: fine)');
  const manual = () => document.dispatchEvent(new Event('storefront:manual'));
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

  let rails = [], timer, scrollFrame = 0;
  const automatic = () => desktop.matches && !reduced.matches && document.documentElement.dataset.motion === 'on' && !document.hidden;
  const visibility = rail => {
    const box = rail.root.getBoundingClientRect();
    if (!box.width || !box.height) return 0;
    const top = Math.max(0, header?.getBoundingClientRect().bottom || 0);
    const height = Math.max(0, Math.min(innerHeight, box.bottom) - Math.max(top, box.top));
    const width = Math.max(0, Math.min(innerWidth, box.right) - Math.max(0, box.left));
    return height * width / (box.height * box.width);
  };
  const selected = () => {
    let best = null, score = 0;
    for (const rail of rails) {
      const visible = visibility(rail);
      if (rail.root.dataset.auto === 'true' && rail.maximum() > 3 && visible >= .5 && visible > score) {
        best = rail; score = visible;
      }
    }
    return best;
  };
  const schedule = () => {
    clearTimeout(timer);
    const effects = document.documentElement.dataset.motion === 'on' && !document.hidden;
    for (const rail of rails) {
      if (!effects) rail.offer?.classList.remove('offer-intro');
      if (effects && rail.offer && !rail.seen && visibility(rail) > 0) {
        rail.seen = true;
        rail.offer.classList.add('offer-intro');
      }
    }
    if (!automatic()) return;
    const active = selected();
    if (!active || active.busy()) return;
    timer = setTimeout(() => {
      if (automatic() && selected() === active && !active.busy()) active.advance();
      schedule();
    }, 6000);
  };
  rails = [...document.querySelectorAll('[data-rail]')].map(root => {
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
      track.classList.remove('is-held');
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
      previous.disabled = position() < 3;
      next.disabled = position() >= maximum() - 3;
      track.classList.toggle('is-draggable', overflows);
      if (meter) {
        meter.hidden = !overflows;
        meter.style.setProperty('--rail-progress', String(Math.min(1, (position() + track.clientWidth) / Math.max(1, track.scrollWidth))));
      }
      schedule();
    };
    previous.addEventListener('click', () => { manual(); step(-1); schedule(); });
    next.addEventListener('click', () => { manual(); step(1); schedule(); });
    const hold = () => {
      if (desktop.matches) {
        track.classList.add('is-held');
        const left = track.scrollLeft;
        // Firefox keeps a smooth scroll alive when its replacement has no distance.
        track.scrollTo({left:left + (rtl ? -1 : 1), behavior:'instant'});
        track.scrollTo({left, behavior:'instant'});
      }
      schedule();
    };
    const release = () => {
      // Restoring snapping between cards would move the visitor's selection.
      schedule();
    };
    for (const event of ['pointerenter','focusin']) root.addEventListener(event, hold);
    root.addEventListener('pointerleave', release);
    root.addEventListener('focusout', () => queueMicrotask(release));
    track.addEventListener('wheel', event => {
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) manual();
      schedule();
    }, {passive:true});
    track.addEventListener('scroll', sync, {passive:true});
    track.addEventListener('keydown', event => {
      if (event.target !== track) return;
      const forward = rtl ? 'ArrowLeft' : 'ArrowRight';
      const backward = rtl ? 'ArrowRight' : 'ArrowLeft';
      if (![forward, backward, 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      manual();
      if (event.key === 'Home') moveTo(0);
      else if (event.key === 'End') moveTo(maximum());
      else step(event.key === forward ? 1 : -1);
      schedule();
    });
    track.addEventListener('dragstart', event => event.preventDefault());
    track.addEventListener('pointerdown', event => {
      suppressClick = false;
      if (event.pointerType === 'mouse' && event.button === 0 && maximum() >= 3) {
        drag = {id:event.pointerId, x:event.clientX, scroll:track.scrollLeft, moved:false};
      }
      schedule();
    });
    track.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      if (!(event.buttons & 1)) { endDrag(event); return; }
      const delta = event.clientX - drag.x;
      if (!drag.moved && Math.abs(delta) <= 6) return;
      if (!drag.moved) manual();
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
    const offer = root.closest('.v-offers-band');
    offer?.addEventListener('animationend', () => offer.classList.remove('offer-intro'));
    return {root, maximum, sync, offer, seen:false, busy:()=>!!drag || root.matches(':hover,:focus-within'), advance:()=>position() >= maximum()-3 ? moveTo(0,true) : step(1)};
  });
  window.addEventListener('scroll', () => {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(() => {scrollFrame = 0; schedule();});
  }, {passive:true});
  window.addEventListener('resize', schedule);
  for (const event of ['visibilitychange','storefront:motion']) document.addEventListener(event, schedule);
  reduced.addEventListener('change', schedule);
  desktop.addEventListener('change', schedule);
  document.fonts.ready.then(schedule);
  rails.forEach(rail => rail.sync());
})();

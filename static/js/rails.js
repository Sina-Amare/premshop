(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const desktop = matchMedia('(min-width: 761px) and (hover: hover) and (pointer: fine)');
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
    const toggle = root.querySelector('[data-rail-toggle]');
    const meter = root.querySelector('[data-rail-meter]');
    const cards = [...track.querySelectorAll('.pcard')];
    const rtl = getComputedStyle(track).direction === 'rtl';
    let paused = false, hovered = root.matches(':hover'), visible = false, timer;
    let drag = null, suppressClick = false, toggleWasPaused = null;
    const position = () => Math.max(0, rtl ? -track.scrollLeft : track.scrollLeft);
    const maximum = () => Math.max(0, track.scrollWidth - track.clientWidth);
    const stops = () => {
      const box = track.getBoundingClientRect(), offset = position();
      return cards.map(card => {
        const rect = card.getBoundingClientRect();
        return Math.min(maximum(), Math.max(0, offset + (rtl ? box.right - rect.right : rect.left - box.left)));
      });
    };
    const moveTo = value => track.scrollTo({left: (rtl ? -1 : 1) * value, behavior: reduced.matches ? 'instant' : 'smooth'});
    const step = direction => {
      const points = stops(), here = position();
      const target = direction > 0 ? points.find(value => value > here + 3) : points.reverse().find(value => value < here - 3);
      moveTo(target === undefined ? (direction > 0 ? maximum() : 0) : target);
    };
    const sync = () => {
      clearTimeout(timer);
      const overflows = maximum() > 3;
      controls.hidden = !overflows;
      if (meter) {
        meter.hidden = !overflows;
        const progress = Math.min(1, (position() + track.clientWidth) / Math.max(1, track.scrollWidth));
        meter.style.setProperty('--rail-progress', String(progress));
      }
      previous.disabled = position() < 3;
      next.disabled = position() >= maximum() - 3;
      track.classList.toggle('is-draggable', overflows);
      const automatic = !!toggle && desktop.matches && !reduced.matches && overflows;
      if (toggle) {
        const ended = overflows && next.disabled;
        if (automatic && ended) paused = true;
        toggle.hidden = !automatic;
        toggle.disabled = ended;
        toggle.setAttribute('aria-pressed', String(paused));
        toggle.setAttribute('aria-label', ended ? 'پایان ریل محصولات' : paused ? 'شروع حرکت خودکار' : 'توقف حرکت خودکار');
        toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
      }
      if (automatic && !paused && !hovered && visible && !document.hidden && !next.disabled) {
        timer = setTimeout(() => { step(1); }, 6000);
      }
    };
    const stop = () => { paused = true; sync(); };
    previous.addEventListener('click', () => { stop(); step(-1); });
    next.addEventListener('click', () => { stop(); step(1); });
    if (toggle) {
      toggle.addEventListener('pointerdown', () => { toggleWasPaused = paused; });
      toggle.addEventListener('click', event => {
        paused = event.detail && toggleWasPaused !== null ? !toggleWasPaused : !paused;
        toggleWasPaused = null;
        sync();
      });
    }
    root.addEventListener('pointerenter', () => { hovered = true; sync(); });
    root.addEventListener('pointerleave', () => { hovered = false; sync(); });
    root.addEventListener('focusin', stop);
    track.addEventListener('wheel', stop, {passive:true});
    track.addEventListener('scroll', sync, {passive:true});
    track.addEventListener('keydown', event => {
      if (event.target !== track) return;
      const forward = rtl ? 'ArrowLeft' : 'ArrowRight';
      const backward = rtl ? 'ArrowRight' : 'ArrowLeft';
      if (![forward, backward, 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      stop();
      if (event.key === 'Home') moveTo(0);
      else if (event.key === 'End') moveTo(maximum());
      else step(event.key === forward ? 1 : -1);
    });
    track.addEventListener('dragstart', event => event.preventDefault());
    track.addEventListener('pointerdown', event => {
      stop();
      suppressClick = false;
      if (event.pointerType !== 'mouse' || event.button !== 0 || maximum() < 3) return;
      drag = {id:event.pointerId, x:event.clientX, scroll:track.scrollLeft, moved:false};
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
    track.addEventListener('pointerup', endDrag);
    track.addEventListener('pointercancel', endDrag);
    track.addEventListener('lostpointercapture', endDrag);
    track.addEventListener('click', event => {
      if (suppressClick) { event.preventDefault(); event.stopPropagation(); }
    }, true);
    new ResizeObserver(sync).observe(track);
    new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting;
      sync();
    }, {threshold:0}).observe(root);
    document.addEventListener('visibilitychange', sync);
    reduced.addEventListener('change', sync);
    desktop.addEventListener('change', sync);
    sync();
  });
})();

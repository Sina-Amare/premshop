(() => {
  const scene = document.querySelector('[data-hero-scene]');
  if (!scene) return;
  const track = scene.querySelector('.nebula');
  const items = [...track.querySelectorAll('.nebula__item')];
  const controls = scene.querySelector('[data-orbit-controls]');
  const previous = scene.querySelector('[data-orbit-prev]');
  const next = scene.querySelector('[data-orbit-next]');
  const range = scene.querySelector('[data-orbit-range]');
  const toggle = scene.querySelector('[data-orbit-toggle]');
  const desktop = matchMedia('(min-width:1200px) and (pointer:fine) and (prefers-reduced-motion:no-preference)');
  const digits = new Intl.NumberFormat('fa-IR');
  let start = 0, size = 6, group = [], points = [], orbital = false;
  let visible = false, hovered = false, phase = -Math.PI / 2, frame = 0, last = 0;
  let paused = false;
  try { paused = localStorage.getItem('premshop.orbitPaused') === 'true'; } catch {}
  const positions = angle => points.map((point, i) => {
    const theta = angle + i * Math.PI * 2 / group.length;
    return {x: point.cx + Math.cos(theta) * point.rx, y: point.cy + Math.sin(theta) * point.ry, w:point.w, h:point.h};
  });
  const paint = () => positions(phase).forEach((p, i) => {
    group[i].style.transform = `translate(${p.x}px, ${p.y}px)`;
  });
  const tick = time => {
    if (last) phase += (time - last) * Math.PI * 2 / 240000;
    last = time;
    paint();
    frame = requestAnimationFrame(tick);
  };
  const update = () => {
    const eligible = orbital && desktop.matches;
    const moving = eligible && document.documentElement.dataset.motion === 'on';
    const running = moving && !paused && visible && !hovered && !document.hidden && !scene.querySelector('.nebula__item:focus-visible');
    toggle.hidden = !eligible;
    toggle.querySelector('span').textContent = paused ? 'ادامه‌ی حرکت' : 'توقف حرکت';
    toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
    scene.classList.toggle('is-animated', moving);
    scene.classList.toggle('is-paused', !running);
    if (running && !frame) frame = requestAnimationFrame(tick);
    if (!running && frame) {cancelAnimationFrame(frame); frame = 0; last = 0;}
  };
  const layout = () => {
    const focusIndex = items.indexOf(document.activeElement);
    size = 6;
    if (desktop.matches) {
      // Choose one capacity for the whole catalog so paging never repeats items.
      scene.dataset.orbitCapacity = '8';
      scene.classList.add('is-orbital');
      items.forEach(item => {item.hidden=false;item.style.transform='';});
      const width=track.clientWidth, height=track.clientHeight;
      const maxW=Math.max(0,...items.map(item=>item.offsetWidth));
      const maxH=Math.max(0,...items.map(item=>item.offsetHeight));
      const rx=(width-maxW)/2-12, ry=(height-maxH)/2-12, chord=2*Math.sin(Math.PI/8);
      if (items.length<=3 || (rx>0 && ry>0 && ((maxW+16)/(chord*rx))**2 + ((maxH+16)/(chord*ry))**2 <= 1)) size=8;
    }
    scene.dataset.orbitCapacity=String(size);
    if (focusIndex >= 0) start = Math.floor(focusIndex / size) * size;
    else start = Math.min(Math.floor(start / size) * size, Math.max(0, Math.ceil(items.length / size) - 1) * size);
    group = items.slice(start, start + size);
    items.forEach((item, i) => {item.hidden = i < start || i >= start + size; item.style.transform = '';});
    controls.hidden = items.length <= size;
    previous.disabled = start === 0;
    next.disabled = start + size >= items.length;
    range.textContent = items.length ? `${digits.format(start+1)}–${digits.format(start+group.length)} / ${digits.format(items.length)}` : '۰ / ۰';
    range.setAttribute('aria-label', `${digits.format(start+1)} تا ${digits.format(start+group.length)} از ${digits.format(items.length)} سرویس`);
    orbital = desktop.matches && group.length > 3;
    scene.classList.toggle('is-orbital', orbital);
    points = group.map(item => ({w:item.offsetWidth, h:item.offsetHeight}));
    const width = track.clientWidth, height = track.clientHeight;
    const maxW = Math.max(0, ...points.map(p => p.w));
    const maxH = Math.max(0, ...points.map(p => p.h));
    points.forEach(p => Object.assign(p, {cx:(width-p.w)/2, cy:(height-p.h)/2, rx:(width-maxW)/2-12, ry:(height-maxH)/2-12}));
    // A complete ellipse must keep the padded rectangles apart at every angle.
    if (orbital) for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const chord = 2 * Math.sin((j-i) * Math.PI / points.length);
        const a = points[i], b = points[j];
        const horizontal = ((a.w+b.w)/2+16) / (chord*a.rx);
        const vertical = ((a.h+b.h)/2+16) / (chord*a.ry);
        if (a.rx <= 0 || a.ry <= 0 || horizontal**2 + vertical**2 > 1) orbital = false;
      }
    }
    scene.classList.toggle('is-orbital', orbital);
    scene.dataset.orbitMode = orbital ? 'orbit' : 'still';
    if (orbital) paint();
    update();
  };
  previous.addEventListener('click', () => {start -= size; layout();});
  next.addEventListener('click', () => {start += size; layout();});
  toggle.addEventListener('click', () => {
    paused = !paused;
    try { localStorage.setItem('premshop.orbitPaused', String(paused)); } catch {}
    update();
  });
  scene.addEventListener('focusin', update);
  // Pointer events precede hover styles in Firefox.
  for (const item of items) {
    const mark = item.querySelector('.v-service-mark');
    mark.addEventListener('pointerenter', () => {hovered = true; update();});
    mark.addEventListener('pointerleave', () => {hovered = false; update();});
  }
  scene.addEventListener('focusout', () => queueMicrotask(update));
  desktop.addEventListener('change', layout);
  document.addEventListener('visibilitychange', update);
  document.addEventListener('storefront:motion', update);
  new IntersectionObserver(([entry]) => {visible = entry.isIntersecting; update();}).observe(scene);
  new ResizeObserver(layout).observe(scene);
  document.fonts.ready.then(layout);
  layout();
})();

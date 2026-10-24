(() => {
  const scene = document.querySelector('[data-hero-scene]');
  if (!scene) return;
  const track = scene.querySelector('.nebula');
  const items = [...track.querySelectorAll('.nebula__item')];
  const controls = scene.querySelector('[data-orbit-controls]');
  const previous = scene.querySelector('[data-orbit-prev]');
  const next = scene.querySelector('[data-orbit-next]');
  const range = scene.querySelector('[data-orbit-range]');
  const toggle = scene.querySelector('[data-hero-toggle]');
  const desktop = matchMedia('(min-width:1200px) and (pointer:fine) and (prefers-reduced-motion:no-preference)');
  const digits = new Intl.NumberFormat('fa-IR');
  let start = 0, size = 6, group = [], points = [], orbital = false;
  let paused = false, visible = false, phase = -Math.PI / 2, frame = 0, last = 0;
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
    const running = eligible && !paused && visible && !document.hidden && !scene.matches(':hover,:focus-within');
    toggle.hidden = !eligible;
    toggle.setAttribute('aria-pressed', String(paused));
    toggle.querySelector('span').textContent = paused ? 'ادامه حرکت' : 'توقف حرکت';
    toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
    scene.classList.toggle('is-animated', eligible);
    scene.classList.toggle('is-paused', !running);
    if (running && !frame) frame = requestAnimationFrame(tick);
    if (!running && frame) {cancelAnimationFrame(frame); frame = 0; last = 0;}
  };
  const layout = () => {
    const focusIndex = items.indexOf(document.activeElement);
    size = innerWidth >= 1200 ? 8 : 6;
    if (focusIndex >= 0) start = Math.floor(focusIndex / size) * size;
    else start = Math.min(Math.floor(start / size) * size, Math.max(0, Math.ceil(items.length / size) - 1) * size);
    group = items.slice(start, start + size);
    items.forEach((item, i) => {item.hidden = i < start || i >= start + size; item.style.transform = '';});
    controls.hidden = items.length <= size;
    previous.disabled = start === 0;
    next.disabled = start + size >= items.length;
    range.textContent = items.length ? `${digits.format(start + 1)} تا ${digits.format(start + group.length)} از ${digits.format(items.length)} سرویس` : 'فعلاً سرویسی نداریم';
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
  previous.addEventListener('click', () => {start -= size; paused = true; layout();});
  next.addEventListener('click', () => {start += size; paused = true; layout();});
  toggle.addEventListener('click', () => {paused = !paused; update();});
  scene.addEventListener('focusin', event => {if (!toggle.contains(event.target)) paused = true; update();});
  for (const event of ['pointerenter','pointerleave']) scene.addEventListener(event, update);
  scene.addEventListener('focusout', () => queueMicrotask(update));
  desktop.addEventListener('change', layout);
  document.addEventListener('visibilitychange', update);
  new IntersectionObserver(([entry]) => {visible = entry.isIntersecting; update();}).observe(scene);
  new ResizeObserver(layout).observe(scene);
  document.fonts.ready.then(layout);
  layout();
})();

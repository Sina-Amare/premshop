(() => {
  const scene = document.querySelector('[data-hero-scene]');
  if (!scene) return;
  const stage = scene.querySelector('.po-stage');
  const core = scene.querySelector('.po-core');
  const beam = scene.querySelector('.po-beam');
  const nodes = [...scene.querySelectorAll('.po-node')];
  const outer = nodes.filter(node => !node.classList.contains('po-node--inner')).length;
  const wide = matchMedia('(min-width: 1200px)');
  const born = performance.now();
  // Each service's centre now and from each second a discount ends at, counted from this
  // load as the cards' countdowns are, never by the visitor's clock.
  const steps = nodes.map(node => [...node.querySelectorAll('template[data-from]')]);
  const step = i => steps[i].filter(t => +t.dataset.from <= (performance.now() - born) / 1000).pop();
  const priced = nodes.map((node, i) => step(i));
  let lit = 0, turn = -Math.PI / 2, last = 0, frame = 0, cycle = 0;
  let size = 0, plates = [], visible = false, pointed = false, pill = false;

  // One rigid constellation: each inner logo sits in a gap between two outer ones, so no
  // name ever crosses another logo, whatever the angle.
  const angle = i => turn + (i < outer ? i * 2 * Math.PI / outer : Math.PI / outer + (i - outer) * 2 * Math.PI / (nodes.length - outer));
  const radius = i => (i < outer ? 272 : 160);
  const aim = () => {
    beam.setAttribute('x2', (320 + Math.cos(angle(lit)) * radius(lit)).toFixed(1));
    beam.setAttribute('y2', (320 + Math.sin(angle(lit)) * radius(lit)).toFixed(1));
  };
  const paint = () => {
    // The logo's centre sits on its ring and its name hangs below it.
    nodes.forEach((node, i) => {
      const r = radius(i) * size / 640;
      node.style.transform = `translate(${(Math.cos(angle(i)) * r).toFixed(1)}px, ${(size / 2 + Math.sin(angle(i)) * r - plates[i] / 2).toFixed(1)}px)`;
    });
    aim();
  };
  const show = i => {
    lit = i;
    nodes.forEach((node, k) => node.classList.toggle('is-lit', k === i));
    stage.style.setProperty('--brand', nodes[i].style.getPropertyValue('--brand'));
    core.replaceChildren(step(i).content.cloneNode(true));
    aim();
  };
  const tick = time => {
    if (last) {
      turn += (time - last) * 2 * Math.PI / 150000;
      paint();
    }
    last = time;
    frame = requestAnimationFrame(tick);
  };
  const update = () => {
    const held = pointed || pill || scene.contains(document.activeElement);
    // Below 1200px the card stands still: it changes only for a visitor.
    const live = wide.matches && document.documentElement.dataset.motion === 'on' && visible && !document.hidden && !held;
    if (live && !frame) frame = requestAnimationFrame(tick);
    if (!live && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
      last = 0;
    }
    if (live && !cycle) cycle = setInterval(() => show((lit + 1) % nodes.length), 3400);
    if (!live && cycle) {
      clearInterval(cycle);
      cycle = 0;
    }
  };
  const layout = () => {
    if (wide.matches) {
      size = stage.clientWidth;
      stage.style.setProperty('--s', (size / 640).toFixed(4));
      plates = nodes.map(node => node.querySelector('.po-plate').offsetHeight);
      paint();
    } else nodes.forEach(node => { node.style.transform = ''; });
    update();
  };

  nodes.forEach((node, i) => {
    const link = node.querySelector('a');
    link.addEventListener('pointerenter', () => { pointed = true; show(i); update(); });
    link.addEventListener('pointerleave', () => { pointed = false; update(); });
    link.addEventListener('focus', () => show(i));
  });
  // Reaching for the centre's link must not swap the service under the pointer.
  core.addEventListener('pointerenter', () => { pointed = true; update(); });
  core.addEventListener('pointerleave', () => { pointed = false; update(); });
  scene.addEventListener('focusin', update);
  scene.addEventListener('focusout', () => queueMicrotask(update));
  // A department pill in the copy lights its own services.
  document.querySelectorAll('.v-hero__chip[data-dept]').forEach(chip => {
    const first = nodes.findIndex(node => node.dataset.dept === chip.dataset.dept);
    if (first < 0) return;
    chip.addEventListener('pointerenter', () => {
      nodes.forEach(node => node.classList.toggle('is-dim', node.dataset.dept !== chip.dataset.dept));
      pill = true;
      show(first);
      update();
    });
    chip.addEventListener('pointerleave', () => {
      nodes.forEach(node => node.classList.remove('is-dim'));
      pill = false;
      update();
    });
  });
  if (steps.some(list => list.length > 1)) setInterval(() => nodes.forEach((node, i) => {
    const now = step(i);
    if (now === priced[i]) return;
    priced[i] = now;
    node.querySelector('a .po-price').replaceWith(now.content.querySelector('.po-price').cloneNode(true));
    if (i === lit) show(i);
  }), 1000);
  document.addEventListener('visibilitychange', update);
  document.addEventListener('storefront:motion', update);
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); }).observe(scene);
  new ResizeObserver(layout).observe(stage);
  scene.classList.add('is-orbit');
  layout();
})();

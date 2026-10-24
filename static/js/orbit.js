(() => {
  const scene = document.querySelector('[data-hero-scene]');
  if (!scene) return;
  const toggle = scene.querySelector('[data-hero-toggle]');
  const desktop = matchMedia('(min-width: 1001px) and (prefers-reduced-motion: no-preference)');
  let paused = false;
  let visible = false;
  const update = () => {
    toggle.hidden = !desktop.matches;
    toggle.setAttribute('aria-pressed', String(paused));
    toggle.querySelector('span').textContent = paused ? 'ادامه حرکت' : 'توقف حرکت';
    toggle.querySelector('use').setAttribute('href', paused ? '#i-play' : '#i-pause');
    scene.classList.toggle('is-animated', desktop.matches);
    scene.classList.toggle('is-paused', paused || !visible || document.hidden || scene.matches(':hover, :focus-within'));
  };
  toggle.addEventListener('click', () => { paused = !paused; update(); });
  for (const event of ['pointerenter', 'pointerleave', 'focusin']) scene.addEventListener(event, update);
  scene.addEventListener('focusout', () => queueMicrotask(update));
  desktop.addEventListener('change', update);
  document.addEventListener('visibilitychange', update);
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); }).observe(scene);
  update();
})();

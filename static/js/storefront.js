(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const sync = () => {
    document.documentElement.dataset.motion = !reduced.matches && !document.querySelector('dialog[open]') ? 'on' : 'off';
    document.dispatchEvent(new Event('storefront:motion'));
  };
  reduced.addEventListener('change', sync);

  const dialog = document.getElementById('quick-plans');
  if (dialog && typeof dialog.showModal === 'function') {
    const sections = [...dialog.querySelectorAll('[data-quick-product]')];
    const title = dialog.querySelector('#quick-plans-title bdi');
    let opener = null;
    document.querySelectorAll('[data-quick-open]').forEach(button => {
      const section = sections.find(item => item.dataset.quickProduct === button.dataset.quickOpen);
      if (!section) return;
      button.hidden = false;
      button.addEventListener('click', () => {
        opener = button;
        sections.forEach(item => { item.hidden = item !== section; });
        title.textContent = section.dataset.name;
        dialog.showModal();
        dialog.querySelector('.quick-plans__body').scrollTop = 0;
        document.body.classList.add('quick-plans-open');
        sync();
      });
    });
    dialog.querySelector('[data-quick-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const controls = [...dialog.querySelectorAll('button,a[href]')].filter(item => !item.disabled && item.getClientRects().length);
      if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === controls.at(-1)) { event.preventDefault(); controls[0].focus(); }
    });
    let outside = false;
    const isOutside = event => {
      const box = dialog.getBoundingClientRect();
      return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
    };
    dialog.addEventListener('pointerdown', event => { outside = isOutside(event); });
    dialog.addEventListener('click', event => { if (outside && isOutside(event)) dialog.close(); });
    dialog.addEventListener('close', () => {
      document.body.classList.remove('quick-plans-open');
      opener?.focus({preventScroll:true});
      sync();
    });
  }

  // On a phone the full header is a fifth of the screen: it steps aside while
  // the reader scrolls down and comes back as soon as they scroll up.
  const header = document.querySelector('.v-header');
  const phone = matchMedia('(max-width: 760px)');
  let lastY = scrollY;
  // An in-page jump lands below the header (scroll-padding), so the header stays for it.
  let jumpUntil = location.hash ? performance.now() + 1500 : 0;
  const jumping = () => { jumpUntil = performance.now() + 1500; };
  addEventListener('hashchange', jumping);
  document.addEventListener('click', event => { if (event.target.closest('a[href*="#"]')) jumping(); });
  // Focus inside the header, a keyboard user or a returned focus, brings it back first.
  header?.addEventListener('focusin', () => header.classList.remove('v-header--away'));
  if (header) addEventListener('scroll', () => {
    const y = scrollY;
    if (performance.now() < jumpUntil) { lastY = y; return; }
    if (Math.abs(y - lastY) < 8) return;
    const busy = header.contains(document.activeElement) || header.querySelector('details[open]');
    header.classList.toggle('v-header--away', phone.matches && y > lastY && y > header.offsetHeight && !busy);
    lastY = y;
  }, {passive: true});

  // The buy bar repeats the buy box's button while that button is out of sight. It mirrors
  // the button's state and never decides one, so it cannot offer what the box would refuse.
  const bar = document.querySelector('[data-buy-bar]');
  const cta = document.querySelector('.buybox__cta');
  if (bar && cta) {
    const add = bar.querySelector('button');
    const error = cta.form.querySelector('.cart-add-error');
    let away = false;
    // The checked plan's own row is the source, so the bar shows the price the server rendered.
    const fill = () => {
      const plan = cta.form.querySelector('[name=plan]:checked')?.closest('.plan');
      if (!plan) return;
      bar.querySelector('bdi').textContent = plan.querySelector('.plan__title').textContent;
      bar.querySelector('.price').replaceChildren(...[...plan.querySelectorAll('[data-plan-price] :is(.price__num,.price__cur)')].map(part => part.cloneNode(true)));
    };
    const update = () => {
      fill();
      add.setAttribute('aria-disabled', cta.getAttribute('aria-disabled') ?? 'false');
      bar.hidden = !(phone.matches && away && !cta.disabled && !cta.hidden && cta.textContent.trim() === add.textContent.trim());
      if (bar.hidden && bar.contains(document.activeElement)) (cta.hidden ? cta.form.querySelector('.buybox__cart') : cta)?.focus({preventScroll: true});
    };
    const sight = new IntersectionObserver(entries => { away = !entries.at(-1).isIntersecting; update(); });
    sight.observe(cta);
    // Observed afresh after every change: a button shown again where it was reports nothing by itself.
    new MutationObserver(() => { sight.unobserve(cta); sight.observe(cta); })
      .observe(cta, {attributes: true, childList: true, characterData: true, subtree: true});
    phone.addEventListener('change', update);
    ['change', 'cart:availability'].forEach(type => cta.form.addEventListener(type, fill));
    document.addEventListener('prices:changed', fill);
    // A refusal is written in the box, out of sight while the bar shows, so the page goes to it.
    new MutationObserver(() => { if (!error.hidden && !bar.hidden) error.scrollIntoView({block: 'center'}); })
      .observe(error, {attributes: true, attributeFilter: ['hidden']});
  }
  sync();
})();

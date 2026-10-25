(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const preference = document.querySelector('[data-motion-preference]');
  const settings = document.querySelector('[data-motion-settings]');
  const hint = document.querySelector('[data-motion-hint]');
  let automatic = true;
  try { automatic = localStorage.getItem('premshop.motion') !== 'off'; } catch {}
  const sync = () => {
    if (preference) {
      preference.checked = automatic && !reduced.matches;
      preference.disabled = reduced.matches;
      hint.hidden = !reduced.matches;
    }
    document.documentElement.dataset.motion = automatic && !reduced.matches && !document.querySelector('dialog[open]') ? 'on' : 'off';
    document.dispatchEvent(new Event('storefront:motion'));
  };
  if (settings) settings.hidden = false;
  preference?.addEventListener('change', () => {
    automatic = preference.checked;
    try { localStorage.setItem('premshop.motion', automatic ? 'on' : 'off'); } catch {}
    sync();
  });
  reduced.addEventListener('change', sync);
  window.addEventListener('storage', event => {
    if (event.key === 'premshop.motion' || event.key === null) {
      try { automatic = localStorage.getItem('premshop.motion') !== 'off'; } catch {}
      sync();
    }
  });

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
  sync();
})();

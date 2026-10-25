(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let browsing = false;
  const sync = () => {
    document.documentElement.dataset.motion = !browsing && !reduced.matches && !document.querySelector('dialog[open]') ? 'on' : 'off';
    document.dispatchEvent(new Event('storefront:motion'));
  };
  document.addEventListener('storefront:manual', () => {
    browsing = true;
    sync();
  });
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
  sync();
})();

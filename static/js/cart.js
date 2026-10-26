(() => {
  const form = document.querySelector('[data-cart-add]:not([data-snapshot])');
  const lines = [...document.querySelectorAll('[data-cart-plan]')];
  if (!form && !lines.length) return;
  const toast = document.getElementById('cart-toast');
  const announcement = document.getElementById('cart-announcement');
  const positionToast = () => {
    const bottom = document.querySelector('.v-header').getBoundingClientRect().bottom;
    toast.style.setProperty('--toast-top', Math.ceil(bottom) + 12 + 'px');
  };
  new ResizeObserver(positionToast).observe(document.querySelector('.v-header'));
  window.addEventListener('scroll', () => { if (!toast.hidden) positionToast(); }, {passive: true});
  let timer, generation = 0, refreshing = false;
  const hide = () => {
    if (!toast.matches(':hover') && !toast.contains(document.activeElement)) toast.hidden = true;
  };
  const scheduleHide = () => { clearTimeout(timer); timer = setTimeout(hide, 6500); };
  const show = (message, error = false) => {
    toast.querySelector('[data-toast-message]').textContent = message;
    toast.querySelector('.cart-toast__message use').setAttribute('href', error ? '#i-info' : '#i-check');
    toast.classList.toggle('cart-toast--error', error);
    positionToast();
    toast.hidden = false;
    announcement.textContent = message;
    scheduleHide();
  };
  toast.querySelector('[data-toast-close]').addEventListener('click', () => {
    toast.hidden = true;
    document.querySelector('.v-cart')?.focus();
  });
  toast.addEventListener('mouseleave', scheduleHide);
  toast.addEventListener('focusout', scheduleHide);

  const sync = data => {
    const badge = document.querySelector('.v-cart__count');
    if (badge) badge.textContent = data.count_text;
    document.querySelector('.v-cart')?.setAttribute('aria-label', data.count_label);
    if (form) {
      const needsControls = [...form.querySelectorAll('[name=plan]')].some(radio => {
        const option = data.options[radio.value];
        return option && ((option.max_add > 1 && !form.querySelector('#qty')) ||
          (option.selectable && radio.closest('.plan').classList.contains('plan--off')));
      });
      if (needsControls) { location.reload(); return; }
      form.querySelectorAll('[name=plan]').forEach(radio => {
        const option = data.options[radio.value];
        if (!option) { radio.disabled = true; radio.dataset.max = '0'; return; }
        radio.dataset.max = option.max_add;
        radio.dataset.inCart = option.in_cart;
        radio.disabled = !option.selectable;
        radio.closest('.plan').classList.toggle('plan--off', radio.disabled);
      });
      if (!form.querySelector('[name=plan]:checked:not(:disabled)')) {
        form.querySelector('[name=plan]:not(:disabled)')?.click();
      }
      form.dispatchEvent(new Event('cart:availability'));
    }
    let changed = false;
    lines.forEach(line => {
      const option = data.options[line.dataset.cartPlan];
      if (!option) { changed ||= line.dataset.cartMaximum !== '0'; return; }
      changed ||= +line.dataset.cartMaximum !== option.maximum;
      changed ||= (line.dataset.cartExpired === 'true') !== option.expired;
      const input = line.querySelector('[name=quantity]');
      if (input) {
        input.max = Math.max(1, option.maximum);
        const increase = line.querySelector('[name=step][value="1"]');
        if (increase) increase.disabled = +input.value >= option.maximum;
      }
    });
    if (changed && !document.activeElement?.closest('.cart-quantity')) location.reload();
  };
  const ids = form ? [...form.querySelectorAll('[name=plan]')].map(radio => radio.value) : lines.map(line => line.dataset.cartPlan);
  const query = new URLSearchParams(ids.map(id => ['plan', id]));
  const refresh = async () => {
    if (document.hidden || refreshing || form?.dataset.pending === 'true') return;
    refreshing = true;
    const started = generation;
    try {
      const response = await fetch('/cart/availability/?' + query, {headers: {Accept: 'application/json'}, cache: 'no-store'});
      if (response.ok && started === generation) sync(await response.json());
    } catch { /* A stale display cannot bypass the server's stock check. */ }
    finally { refreshing = false; }
  };
  if (form && window.fetch) form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.pending === 'true') return;
    const button = form.querySelector('.buybox__cta');
    if (button.disabled) return;
    const body = new FormData(form);
    generation += 1;
    form.dataset.pending = 'true';
    button.disabled = true;
    form.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(form.action, {method: 'POST', body, headers: {Accept: 'application/json'}});
      const data = await response.json();
      if (response.ok) {
        sync(data);
        show(data.message);
      } else show(data.message || 'صفحه را دوباره باز کنید و دوباره امتحان کنید.', true);
    } catch {
      show('نتیجه مشخص نیست. سبد خریدتان را بررسی کنید.', true);
    } finally {
      form.dataset.pending = 'false';
      form.removeAttribute('aria-busy');
      form.dispatchEvent(new Event('cart:availability'));
      refresh();
    }
  });

  const clocks = [...document.querySelectorAll('[data-reservation-seconds]')].map(node => ({
    node, ends: performance.now() + +node.dataset.reservationSeconds * 1000
  }));
  if (clocks.length) setInterval(() => {
    clocks.forEach(({node, ends}) => {
      const left = Math.max(0, Math.ceil((ends - performance.now()) / 1000));
      const text = String(Math.floor(left / 60)).padStart(2, '0') + ':' + String(left % 60).padStart(2, '0');
      node.querySelector('[data-reservation-clock]').textContent = text.replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]);
      if (!left) location.reload();
    });
  }, 1000);
  setInterval(refresh, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();

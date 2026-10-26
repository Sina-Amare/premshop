(() => {
  const form = document.querySelector('[data-cart-add]:not([data-snapshot])');
  const cartPage = document.querySelector('.cart-page');
  const lines = () => [...document.querySelectorAll('[data-cart-plan]')];
  if (!form && !lines().length) return;
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
  const show = data => {
    toast.querySelector('[data-toast-message]').textContent = data.message;
    const hold = toast.querySelector('[data-toast-hold]');
    hold.textContent = data.hold_message || '';
    hold.hidden = !data.hold_message;
    positionToast();
    toast.hidden = false;
    announcement.textContent = [data.message, data.hold_message].filter(Boolean).join(' ');
    scheduleHide();
  };
  toast.querySelector('[data-toast-close]').addEventListener('click', () => {
    toast.hidden = true;
    document.querySelector('.v-cart')?.focus();
  });
  toast.addEventListener('mouseleave', scheduleHide);
  toast.addEventListener('focusout', scheduleHide);

  const updateCart = async started => {
    const response = await fetch('/cart/', {cache: 'no-store'});
    if (!response.ok) return;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const fresh = doc.querySelector('.cart-page');
    if (!fresh || started !== generation) return;
    const edited = [...cartPage.querySelectorAll('[name=quantity]')]
      .filter(input => input.value !== input.defaultValue).map(input => [input.id, input.value]);
    const active = document.activeElement;
    const focusedLine = active?.closest('[data-cart-plan]');
    const focusId = cartPage.contains(active) ? active.id : '';
    const focusSelector = focusedLine && !focusId ? `[data-cart-plan="${focusedLine.dataset.cartPlan}"] ` +
      (active.name === 'step' ? `[name=step][value="${active.value}"]` :
        active.matches('.cart-remove,.cart-apply,.cart-reserve') ? '.' + active.classList[0] : '') : '';
    cartPage.replaceChildren(...fresh.childNodes);
    edited.forEach(([id, value]) => { const input = document.getElementById(id); if (input) input.value = value; });
    const target = focusId ? document.getElementById(focusId) : focusSelector ? cartPage.querySelector(focusSelector) : null;
    target?.focus({preventScroll: true});
    if (focusedLine && !target) {
      const heading = cartPage.querySelector('h1');
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }
  };
  const sync = async (data, forceCart = false) => {
    const badge = document.querySelector('.v-cart__count');
    let changed = badge && badge.textContent !== data.count_text;
    if (badge && changed) badge.textContent = data.count_text;
    const cartLink = document.querySelector('.v-cart');
    if (cartLink?.getAttribute('aria-label') !== data.count_label) cartLink?.setAttribute('aria-label', data.count_label);
    if (form) {
      form.querySelectorAll('[name=plan]').forEach(radio => {
        const option = data.options[radio.value];
        radio.dataset.max = option?.max_add || 0;
        radio.dataset.inCart = option?.in_cart || 0;
        radio.disabled = !option?.selectable;
        const row = radio.closest('.plan');
        row.classList.toggle('plan--off', radio.disabled);
        const price = row.querySelector('[data-plan-price]');
        if (price) {
          price.hidden = radio.disabled;
          if (option && radio.dataset.priceKey !== option.price_key) {
            price.innerHTML = option.price_html;
            radio.dataset.priceKey = option.price_key;
          }
        }
        const unavailable = row.querySelector('.plan__off');
        if (unavailable) unavailable.hidden = !radio.disabled;
      });
      if (!form.querySelector('[name=plan]:checked:not(:disabled)')) {
        const first = form.querySelector('[name=plan]:not(:disabled)');
        if (first) first.checked = true;
      }
      document.dispatchEvent(new Event('prices:updated'));
      form.dispatchEvent(new Event('cart:availability'));
    }
    lines().forEach(line => {
      const option = data.options[line.dataset.cartPlan];
      if (!option) { changed ||= line.dataset.cartMaximum !== '0'; return; }
      changed ||= +line.dataset.cartMaximum !== option.maximum;
      changed ||= (line.dataset.cartExpired === 'true') !== option.expired;
      changed ||= (line.dataset.cartReservationRequired === 'true') !== option.reservation_required;
      changed ||= line.dataset.cartPrice !== option.price_text;
    });
    if (cartPage && (changed || forceCart)) await updateCart(generation);
  };
  const refresh = async (forceCart = false) => {
    if (document.hidden || refreshing || form?.dataset.pending === 'true') return;
    refreshing = true;
    const started = generation;
    const ids = form ? [...form.querySelectorAll('[name=plan]')].map(radio => radio.value) : lines().map(line => line.dataset.cartPlan);
    const query = new URLSearchParams(ids.map(id => ['plan', id]));
    try {
      const response = await fetch('/cart/availability/?' + query, {headers: {Accept: 'application/json'}, cache: 'no-store'});
      if (response.ok && started === generation) await sync(await response.json(), forceCart);
    } catch { /* A stale display cannot bypass the server's stock check. */ }
    finally { refreshing = false; }
  };
  if (form && window.fetch) form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.pending === 'true') return;
    const button = form.querySelector('.buybox__cta');
    if (button.disabled || button.hidden || button.getAttribute('aria-disabled') === 'true') return;
    const error = form.querySelector('.cart-add-error');
    error.hidden = true;
    const body = new FormData(form);
    generation += 1;
    form.dataset.pending = 'true';
    button.setAttribute('aria-disabled', 'true');
    form.setAttribute('aria-busy', 'true');
    const fail = message => { error.textContent = message; error.hidden = false; };
    try {
      const response = await fetch(form.action, {method: 'POST', body, headers: {Accept: 'application/json'}});
      const data = await response.json();
      if (response.ok) {
        await sync(data);
        show(data);
      } else fail(data.message || 'صفحه را دوباره باز کنید و دوباره امتحان کنید.');
    } catch {
      fail('نتیجه مشخص نیست. سبد خریدتان را بررسی کنید.');
    } finally {
      form.dataset.pending = 'false';
      form.removeAttribute('aria-busy');
      form.dispatchEvent(new Event('cart:availability'));
      refresh();
    }
  });

  if (cartPage) setInterval(() => {
    let expired = false;
    cartPage.querySelectorAll('[data-reservation-seconds]').forEach(node => {
      if (!node.dataset.ends) node.dataset.ends = performance.now() + +node.dataset.reservationSeconds * 1000;
      const left = Math.max(0, Math.ceil((+node.dataset.ends - performance.now()) / 1000));
      const text = [Math.floor(left / 3600), Math.floor(left / 60) % 60, left % 60].map(n => String(n).padStart(2, '0')).join(':');
      node.querySelector('[data-reservation-clock]').textContent = text.replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]);
      if (!left) { node.removeAttribute('data-reservation-seconds'); expired = true; }
    });
    if (expired) refresh(true);
  }, 1000);
  setInterval(refresh, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();

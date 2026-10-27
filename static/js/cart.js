(() => {
  const form = document.querySelector('[data-cart-add]:not([data-snapshot])');
  const cartPage = document.querySelector('.cart-page');
  const lines = () => [...document.querySelectorAll('[data-cart-plan]')];
  if (!form && !cartPage) return;
  const toast = document.getElementById('cart-toast');
  const announcement = document.getElementById('cart-announcement');
  const positionToast = () => {
    const bottom = document.querySelector('.v-header').getBoundingClientRect().bottom;
    toast.style.setProperty('--toast-top', Math.ceil(bottom) + 12 + 'px');
  };
  new ResizeObserver(positionToast).observe(document.querySelector('.v-header'));
  window.addEventListener('scroll', () => { if (!toast.hidden) positionToast(); }, {passive: true});
  let timer, generation = 0, refreshing = false, cartPending = false;
  const hide = () => {
    if (!toast.matches(':hover') && !toast.contains(document.activeElement)) toast.hidden = true;
  };
  const scheduleHide = () => { clearTimeout(timer); timer = setTimeout(hide, 6500); };
  const show = data => {
    toast.querySelector('[data-toast-message]').textContent = data.message;
    positionToast();
    toast.hidden = false;
    announcement.textContent = data.message;
    scheduleHide();
  };
  toast.querySelector('[data-toast-close]').addEventListener('click', () => {
    toast.hidden = true;
    document.querySelector('.v-cart')?.focus();
  });
  toast.addEventListener('mouseleave', scheduleHide);
  toast.addEventListener('focusout', scheduleHide);

  const updateStepper = input => {
    const quantity = Number(input.value);
    const controls = input.closest('.cart-quantity');
    controls.querySelector('[name=step][value="-1"]').disabled = quantity <= 1;
    controls.querySelector('[name=step][value="1"]').disabled = !input.validity.valid || quantity >= +input.max;
    const dirty = input.value !== input.defaultValue;
    controls.querySelector('.cart-apply').hidden = !dirty;
    controls.closest('[data-cart-plan]').querySelector('[data-cart-draft]').hidden = !dirty;
  };
  cartPage?.addEventListener('input', event => {
    if (event.target.matches('.cart-quantity [name=quantity]')) {
      event.target.dataset.editVersion = String(Number(event.target.dataset.editVersion || 0) + 1);
      updateStepper(event.target);
    }
  });

  const updateCart = async (started, message = 'سبد خرید به‌روز شد. موجودی و تعداد محصولات را بررسی کنید.', accepted) => {
    const response = await fetch('/cart/', {cache: 'no-store'});
    if (!response.ok) return false;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const fresh = doc.querySelector('.cart-page');
    if (!fresh || started !== generation) return false;
    const edited = [...cartPage.querySelectorAll('[name=quantity]')]
      .filter(input => accepted && input.id === accepted.id ?
        (input.dataset.editVersion || '0') !== accepted.version : input.value !== input.defaultValue)
      .map(input => [input.id, input.value]);
    const errors = [...cartPage.querySelectorAll('.cart-line__error:not([hidden])')]
      .map(error => [error.closest('[data-cart-plan]').dataset.cartPlan, error.textContent]);
    const active = document.activeElement;
    const focusedLine = active?.closest('[data-cart-plan]');
    const focusId = cartPage.contains(active) ? active.id : '';
    const focusHref = cartPage.contains(active) && active.matches('a[href]') ? active.getAttribute('href') : null;
    const focusSelector = focusedLine && !focusId ? `[data-cart-plan="${focusedLine.dataset.cartPlan}"] ` +
      (active.name === 'step' ? `[name=step][value="${active.value}"]` :
        active.matches('.cart-remove,.cart-apply') ? '.' + active.classList[0] : '') : '';
    cartPage.replaceChildren(...fresh.childNodes);
    cartPage.dataset.cartRevision = fresh.dataset.cartRevision;
    edited.forEach(([id, value]) => { const input = document.getElementById(id); if (input) input.value = value; });
    errors.forEach(([id, text]) => {
      const error = cartPage.querySelector(`[data-cart-plan="${Number(id)}"] .cart-line__error`);
      if (error) { error.textContent = text; error.hidden = false; }
    });
    cartPage.querySelectorAll('.cart-quantity [name=quantity]').forEach(updateStepper);
    let target = focusHref !== null ? [...cartPage.querySelectorAll('a[href]')].find(link => link.getAttribute('href') === focusHref) :
      focusId ? document.getElementById(focusId) : focusSelector ? cartPage.querySelector(focusSelector) : null;
    if (target?.disabled && focusedLine) target = cartPage.querySelector(`[data-cart-plan="${focusedLine.dataset.cartPlan}"] [name=quantity]`);
    target?.focus({preventScroll: true});
    if (focusedLine && document.activeElement !== target) {
      const heading = cartPage.querySelector('h1');
      heading.tabIndex = -1;
      heading.focus({preventScroll: true});
    }
    announcement.textContent = message;
    return true;
  };
  const sync = async (data, forceCart = false, message, accepted) => {
    const badge = document.querySelector('.v-cart__count');
    let changed = badge && badge.textContent !== data.count_text;
    if (cartPage) changed ||= cartPage.dataset.cartRevision !== data.revision;
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
        const promotion = row.querySelector('[data-plan-promotion]');
        if (promotion) promotion.hidden = radio.disabled;
        if (price) {
          price.hidden = radio.disabled;
          if (option && radio.dataset.priceKey !== option.price_key) {
            price.innerHTML = option.price_html;
            if (promotion) promotion.innerHTML = option.promotion_html;
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
      changed ||= line.dataset.cartPrice !== option.price_text;
    });
    if (cartPage && (changed || forceCart)) return await updateCart(generation, message, accepted);
  };
  const refresh = async (forceCart = false) => {
    if (document.hidden || refreshing || cartPending || form?.dataset.pending === 'true') return;
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
  cartPage?.addEventListener('submit', async event => {
    const action = event.target;
    if (!action.matches('.cart-quantity,.cart-remove-form') || !window.fetch) return;
    event.preventDefault();
    if (cartPending) return;
    const line = action.closest('[data-cart-plan]');
    const planId = Number(line.dataset.cartPlan);
    const error = line.querySelector('.cart-line__error');
    document.getElementById('cart-action-error')?.remove();
    error.hidden = true;
    const body = new FormData(action);
    const quantity = action.querySelector('[name=quantity]');
    const accepted = quantity ? {id:quantity.id, version:quantity.dataset.editVersion || '0'} : null;
    if (event.submitter?.name) body.append(event.submitter.name, event.submitter.value);
    cartPending = true;
    generation += 1;
    cartPage.setAttribute('aria-busy', 'true');
    cartPage.querySelectorAll('button').forEach(button => button.setAttribute('aria-disabled', 'true'));
    let message = '';
    try {
      const response = await fetch(action.action, {method:'POST', body, headers:{Accept:'application/json'}});
      const data = await response.json();
      if (!response.ok) message = data.message || 'سبد خریدتان را بررسی کنید و دوباره امتحان کنید.';
      else if (!await sync(data, true, data.message, accepted)) message = 'تغییر ثبت شد، اما نمایش سبد به‌روز نشد. صفحه را دوباره باز کنید.';
    } catch {
      message = 'نتیجه مشخص نیست. سبد خریدتان را بررسی کنید.';
    } finally {
      cartPending = false;
      cartPage.removeAttribute('aria-busy');
      cartPage.querySelectorAll('button[aria-disabled]').forEach(button => button.removeAttribute('aria-disabled'));
      if (message) {
        await refresh();
        let target = cartPage.querySelector(`[data-cart-plan="${planId}"] .cart-line__error`);
        if (!target) {
          target = document.createElement('p');
          target.id = 'cart-action-error';
          target.className = 'wrap cart-notice cart-notice--error';
          target.setAttribute('role', 'alert');
          cartPage.before(target);
        }
        target.textContent = message;
        target.hidden = false;
      }
    }
  });
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

  setInterval(refresh, 15000);
  cartPage?.querySelectorAll('.cart-quantity [name=quantity]').forEach(updateStepper);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();

(() => {
  const root = document.querySelector('[data-checkout-login]');
  if (!root) {
    const error = document.querySelector('[data-checkout-error]')
      || document.querySelector('#id_email[aria-invalid="true"]');
    error?.focus();
    return;
  }
  const phone = root.querySelector('[data-inline-phone]');
  const code = root.querySelector('[data-inline-code]');
  const resend = root.querySelector('[data-inline-resend]');
  const sms = root.querySelector('[data-inline-sms]');
  const delivery = root.querySelector('[data-code-delivery]');
  const smsDeliveryText = delivery.querySelector('#delivery-sms-text');
  const firstSmsText = smsDeliveryText.textContent;
  const status = root.querySelector('[data-login-status]');
  const edit = root.querySelector('[data-inline-edit]');
  let resendAt = 0;
  let timer;
  // The code page's six cells, laid over this form's code field once it shows.
  let row;
  const cells = () => {
    if (!row) {
      row = window.codeCells(code, {
        onInput: () => { status.hidden = true; },
        onComplete: () => code.requestSubmit(),
      });
      code.querySelector('[data-code-submit]').hidden = true;
    }
    return row;
  };
  edit.addEventListener('click', () => {
    phone.hidden = false;
    code.hidden = true;
    resend.hidden = true;
    sms.hidden = true;
    delivery.hidden = true;
    edit.hidden = true;
    code.reset();
    if (row) row.clear();
    status.hidden = true;
    phone.querySelector('input[name=phone]').focus();
  });
  function cooldown(seconds) {
    resendAt = Date.now() + seconds * 1000;
    clearInterval(timer);
    const tick = () => {
      const left = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
      const button = resend.querySelector('button');
      button.disabled = left > 0;
      button.textContent = left ? `${left.toLocaleString('fa-IR')} ثانیه تا ارسال دوباره` : 'ارسال دوباره‌ی کد';
      if (!left) clearInterval(timer);
    };
    tick();
    timer = setInterval(tick, 1000);
  }
  for (const form of [phone, code, resend, sms]) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      if (button.disabled) return;
      button.disabled = true;
      form.setAttribute('aria-busy', 'true');
      status.hidden = false;
      status.textContent = 'در حال بررسی…';
      if (form === code) cells().lock();
      try {
        const response = await fetch(form.action, {
          method: 'POST', body: new FormData(form),
          headers: {'Accept': 'application/json'}, credentials: 'same-origin',
        });
        if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (!data.ok) {
          status.textContent = data.error || 'صفحه‌ی ثبت سفارش آماده نشد. دوباره امتحان کنید.';
          if (form === code) cells().fail(() => { row.disable(false); row.focus(); });
          return;
        }
        // Reload after session rotation to obtain the merged cart, CSRF token and quote.
        if (form === code) {
          cells().confirm(() => window.location.assign(window.location.pathname));
          return;
        }
        if (data.next) {
          window.location.assign(window.location.pathname);
          return;
        }
        phone.hidden = true;
        code.hidden = false;
        resend.hidden = false;
        edit.hidden = false;
        status.hidden = true;
        delivery.hidden = false;
        smsDeliveryText.textContent = data.sms_notice || firstSmsText;
        delivery.querySelector('#delivery-sms').hidden = data.channel === 'telegram';
        delivery.querySelector('#delivery-telegram').hidden = data.channel !== 'telegram';
        sms.hidden = data.channel !== 'telegram';
        cooldown(data.cooldown || 120);
        code.reset();
        cells().clear();
        cells().focus();
      } catch {
        status.textContent = 'پاسخی از سایت نرسید. دوباره تلاش کنید یا صفحه‌ی ورود را باز کنید.';
        if (form === code) cells().fail(() => { row.disable(false); row.focus(); });
      } finally {
        form.removeAttribute('aria-busy');
        if (form !== resend || Date.now() >= resendAt) button.disabled = false;
      }
    });
  }
})();

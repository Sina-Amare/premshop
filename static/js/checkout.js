(() => {
  const root = document.querySelector('[data-checkout-login]');
  if (!root) {
    document.querySelector('[data-checkout-error]')?.focus();
    return;
  }
  const email = root.querySelector('[data-inline-email]');
  const code = root.querySelector('[data-inline-code]');
  const resend = root.querySelector('[data-inline-resend]');
  const status = root.querySelector('[data-login-status]');
  const edit = root.querySelector('[data-inline-edit]');
  let resendAt = 0;
  let timer;
  edit.addEventListener('click', () => {
    email.hidden = false;
    code.hidden = true;
    resend.hidden = true;
    edit.hidden = true;
    code.reset();
    status.hidden = true;
    email.querySelector('input[name=email]').focus();
  });
  function cooldown(seconds) {
    resendAt = Date.now() + seconds * 1000;
    clearInterval(timer);
    const tick = () => {
      const left = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
      const button = resend.querySelector('button');
      button.disabled = left > 0;
      button.textContent = left ? `ارسال دوباره تا ${left.toLocaleString('fa-IR')} ثانیه دیگر` : 'ارسال دوباره‌ی کد';
      if (!left) clearInterval(timer);
    };
    tick();
    timer = setInterval(tick, 1000);
  }
  for (const form of [email, code, resend]) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button');
      if (button.disabled) return;
      button.disabled = true;
      form.setAttribute('aria-busy', 'true');
      status.hidden = false;
      status.textContent = 'در حال بررسی…';
      try {
        const response = await fetch(form.action, {
          method: 'POST', body: new FormData(form),
          headers: {'Accept': 'application/json'}, credentials: 'same-origin',
        });
        if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error();
        const data = await response.json();
        if (!data.ok) {
          status.textContent = data.error || 'درخواست انجام نشد. دوباره امتحان کنید.';
          return;
        }
        if (form === code || data.next) {
          // Reload after session rotation to obtain the merged cart, CSRF token and quote.
          window.location.assign(window.location.pathname);
          return;
        }
        email.hidden = true;
        code.hidden = false;
        resend.hidden = false;
        edit.hidden = false;
        status.textContent = 'کد ورود فرستاده شد. کد را از ایمیلتان وارد کنید.';
        cooldown(data.cooldown || 60);
        code.querySelector('input[name=code]').focus();
      } catch {
        status.textContent = 'پاسخ دریافت نشد. دوباره امتحان کنید یا از صفحه‌ی ورود استفاده کنید.';
      } finally {
        form.removeAttribute('aria-busy');
        if (form !== resend || Date.now() >= resendAt) button.disabled = false;
      }
    });
  }
})();

// The code page: six cells over the one code field, the countdown to the
// code's expiry and the resend cooldown. See login_code_verify.html for
// what the page does without it.
(function () {
  var form = document.getElementById('code-form');
  var errorBox = document.getElementById('code-error');
  var clock = document.getElementById('code-clock');
  var status = document.getElementById('code-status');
  var submitButton = document.getElementById('code-submit');
  var resendForm = document.getElementById('resend-form');
  var resendButton = document.getElementById('resend');
  var resendHint = document.getElementById('resend-hint');
  var smsForm = document.getElementById('sms-form');

  var FA = '۰۱۲۳۴۵۶۷۸۹';
  var toFa = function (n) { return String(n).replace(/\d/g, function (d) { return FA[+d]; }); };

  // --- Enhance: the shared cells over the plain field. Without JS none of this runs.
  var busy = false;
  var expired = false;
  var warned = false;
  var row = window.codeCells(form, {
    onInput: function () { showError(''); },
    onComplete: function () { submit(); }
  });
  var cells = row.cells;
  submitButton.hidden = true;               // the sixth digit submits
  row.focus();

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }

  // --- Submit by fetch so the cells can answer before the page changes.
  function submit() {
    if (busy || expired) return;
    busy = true;
    row.lock();
    var body = new FormData(form);
    fetch(form.action || location.href, {
      method: 'POST', body: body, credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function (r) {
      var isJson = (r.headers.get('content-type') || '').indexOf('application/json') === 0;
      return isJson ? r.json() : Promise.reject(new Error('non-json'));
    }).then(function (data) {
      if (data.ok) {
        row.confirm(function () { location.href = data.next; });
        return;
      }
      fail(data.error);
    }).catch(function () {
      // Anything that is not a JSON answer — the 503 page, a network drop —
      // falls back to a plain submit so the server renders what it renders.
      form.submit();
    });
  }
  function fail(message) {
    showError(message || 'کد ورود درست نیست. دوباره تلاش کنید.');
    row.fail(function () {
      tick();
      row.disable(expired);
      (expired ? resendButton : cells[0]).focus();
      busy = false;
    });
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(); });

  // --- Countdown to the server's expiry, and the resend cooldown.
  // Deadlines are set from seconds-remaining the server sent, on this machine's
  // clock. Never from an absolute server time: a customer's clock may be wrong.
  var expiresIn = parseInt(form.getAttribute('data-expires-in'), 10) || 0;
  var expiresAt = Math.floor(Date.now() / 1000) + expiresIn;
  var cooldownSeconds = parseInt(form.getAttribute('data-resend-cooldown'), 10) || 60;
  // Never past the code's own end: a page reloaded with 30 seconds left sent
  // focus, at expiry, to a button still cooling down. One reloaded after its
  // code expired has nothing to wait for. The server's three-per-15-minutes
  // limit stays the defence; this is a courtesy.
  var cooldownUntil = Math.floor(Date.now() / 1000) + Math.min(cooldownSeconds, Math.max(expiresIn, 0));
  var resendPending = false;

  function tick() {
    var now = Math.floor(Date.now() / 1000);
    var left = expiresAt - now;
    var justExpired = false;
    if (left > 0) {
      var m = Math.floor(left / 60), s = left % 60;
      clock.textContent = toFa(m) + ':' + toFa(s < 10 ? '0' + s : s) + ' مانده';
      clock.hidden = false;
      if (left <= 60 && !warned) { warned = true; status.textContent = 'یک دقیقه از اعتبار کد مانده است.'; }
      if (expired) { expired = false; row.disable(false); }
    } else if (!expired) {
      expired = true;
      justExpired = true;
      clock.textContent = 'کد منقضی شد';
      clock.hidden = false;
      clock.classList.replace('badge--time', 'badge--neutral');   // the clock has stopped
      row.disable(true);
      showError('');
      resendHint.textContent = 'برای گرفتن کد جدید، «ارسال دوباره» را بزنید.';
      status.textContent = 'کد منقضی شد. برای گرفتن کد جدید، «ارسال دوباره» را بزنید.';
      resendButton.classList.replace('btn--ghost', 'btn--primary');
    }
    var wait = cooldownUntil - now;
    if (resendPending) {
      resendButton.disabled = true;
      resendButton.textContent = 'در حال ارسال…';
    } else if (wait > 0) {
      resendButton.disabled = true;
      resendButton.textContent = 'ارسال دوباره (' + toFa(wait) + ')';
    } else {
      resendButton.disabled = false;
      resendButton.textContent = 'ارسال دوباره';
    }
    // The cells were just disabled under the customer's cursor; focus goes to
    // the one thing left to do rather than falling to the page body.
    if (justExpired) resendButton.focus();
  }
  tick();
  setInterval(tick, 1000);

  function resendCode(e) {
    var sendForm = e.currentTarget;
    e.preventDefault();
    if (resendPending || (sendForm === resendForm && resendButton.disabled)) return;
    resendPending = true;
    smsForm.querySelector('button').disabled = true;
    tick();
    fetch(sendForm.action, {
      method: 'POST', body: new FormData(sendForm), credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data.ok) {
        expiresAt = Math.floor(Date.now() / 1000) + (data.expires_in || 0);
        cooldownUntil = Math.floor(Date.now() / 1000) + (data.cooldown || cooldownSeconds);
        expired = false;
        warned = false;
        clock.classList.replace('badge--neutral', 'badge--time');
        resendButton.classList.replace('btn--primary', 'btn--ghost');
        showError('');
        row.clear();
        row.disable(false);
        row.focus();
        document.getElementById('delivery-sms').hidden = data.channel === 'telegram';
        document.getElementById('delivery-telegram').hidden = data.channel !== 'telegram';
        smsForm.hidden = data.channel !== 'telegram';
        resendHint.textContent = data.channel === 'telegram' ? '' : 'کد جدید را فرستادیم.';
        status.textContent = data.channel === 'telegram'
          ? document.getElementById('delivery-telegram').textContent : 'کد جدید را فرستادیم.';
      } else {
        showError(data.error);
        cooldownUntil = Math.floor(Date.now() / 1000) + 5;
      }
      resendPending = false;
      smsForm.querySelector('button').disabled = false;
      tick();
    }).catch(function () {
      showError('کد فرستاده نشد. دوباره تلاش کنید.');
      cooldownUntil = Math.floor(Date.now() / 1000) + 5;
      resendPending = false;
      smsForm.querySelector('button').disabled = false;
      tick();
    });
  }
  resendForm.addEventListener('submit', resendCode);
  smsForm.addEventListener('submit', resendCode);
})();

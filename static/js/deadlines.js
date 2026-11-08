// Discount deadlines tick. The server rendered the text already, so a visitor
// with JavaScript off sees the time that was left when the page was built;
// this only keeps it moving. The remaining seconds come from the server and
// the deadline is computed against this machine's clock, never against an
// absolute server time — a customer whose clock is a day out would otherwise
// see a countdown that is a day wrong.
//
// At zero the badge says the discount is over and stops being amber, and each
// price under a deadline swaps in the one the server rendered for that moment
// (data-after, from effective_price), so the page never shows a price that has
// run out; a card swaps the title of the plan it leads with as well. The cart
// and the checkout re-read every price regardless.
(function () {
  var timed = [], timer;

  var FA = '۰۱۲۳۴۵۶۷۸۹';
  var toFa = function (n) { return String(n).replace(/\d/g, function (d) { return FA[+d]; }); };
  var pad = function (n) { return n < 10 ? '0' + n : String(n); };

  // The same shape the server renders (core.formatting.format_clock): hours
  // keep counting past a day.
  function text(left) {
    return toFa(pad(Math.floor(left / 3600))) + ':' + toFa(pad(Math.floor((left % 3600) / 60))) + ':' + toFa(pad(left % 60));
  }

  function register() {
    var start = Math.floor(Date.now() / 1000);
    document.querySelectorAll('[data-left]:not([data-clock-ready])').forEach(function (el) {
      el.setAttribute('data-clock-ready', '');
      el.endsAt = start + (parseInt(el.getAttribute('data-left'), 10) || 0);
      timed.push(el);
    });
    if (timed.length && !timer) timer = setInterval(tick, 1000);
    tick();
  }

  function end(el) {
    if (el.hasAttribute('data-offer-badge')) {
      el.hidden = true;
      return;
    }
    if (el.hasAttribute('data-promotion')) {
      el.setAttribute('data-expired', '');
      el.hidden = true;
      var offer = el.parentElement.querySelector('[data-promotion]:not([data-expired])');
      if (offer) offer.hidden = false;
      return;
    }
    if (el.hasAttribute('data-card-price')) {
      var current = el.closest('.pcard').querySelector('.pcard__price-state');
      current.replaceWith(el.content.cloneNode(true));
      el.remove();
      return;
    }
    if (el.classList.contains('deadline')) {
      el.textContent = 'تخفیف تمام شد';
      el.classList.add('deadline--done');
      var said = el.nextElementSibling;
      if (said && said.classList.contains('deadline__said')) said.textContent = 'تخفیف تمام شد';
      document.dispatchEvent(new Event('deadline:ended'));
      return;
    }
    if (!el.classList.contains('price')) {
      el.textContent = el.getAttribute('data-after');
      return;
    }
    el.querySelector('.price__num').textContent = el.getAttribute('data-after');
    el.querySelectorAll('.price__was, .price__now').forEach(function (node) { node.remove(); });
    document.dispatchEvent(new Event('prices:changed'));
  }

  function tick() {
    var now = Math.floor(Date.now() / 1000);
    timed = timed.filter(function (el) {
      if (!el.isConnected) return false;
      var left = el.endsAt - now;
      if (left <= 0) { end(el); return false; }
      if (el.classList.contains('deadline')) el.textContent = text(left) + ' مانده';
      return true;
    });
    if (!timed.length) { clearInterval(timer); timer = null; }
  }
  document.addEventListener('prices:updated', register);
  register();
})();

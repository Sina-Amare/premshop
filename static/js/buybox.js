// The invoice shows a total it was given and computes no price. The server writes, for
// every plan and every quantity it allows, the amount and the amount in words, now and
// after the promotion ends (the form's JSON script, apps/catalog/invoice.py). This picks
// one, keeps the quantity inside the plan's data-max, moves a changed total in from 4px
// below (.swap; reduced motion makes it instant), switches each plan to its
// post-promotion values at that plan's own end, and keeps a stuck invoice's total and
// button in view.
(function () {
  var FA = '۰۱۲۳۴۵۶۷۸۹';
  function fa(n) { return String(n).replace(/\d/g, function (d) { return FA[d]; }); }
  var LATIN = /([A-Za-z](?:[A-Za-z0-9+&\/'-]|\.(?=[A-Za-z0-9])| (?=[A-Za-z0-9+&\/'-]))*[A-Za-z0-9+]|[A-Za-z])/;

  [].forEach.call(document.querySelectorAll('form.invoice__form'), function (form) {
    var data = JSON.parse(form.querySelector('script[type="application/json"]').textContent);
    var amount = form.querySelector('.invoice__amount');
    var cur = form.querySelector('.invoice__cur');
    var words = form.querySelector('.invoice__words-text');
    var qtyRow = form.querySelector('.invoice__qty');
    var qty = form.querySelector('input[name=quantity]');
    var shown = form.querySelector('.stepper__value');
    var cap = form.querySelector('.invoice__hint');
    var asksRow = form.querySelector('.invoice__asks');
    var asks = form.querySelector('.invoice__asks-v');
    var plans = [].slice.call(form.querySelectorAll('input[name=plan]'));
    var ended = {};  // plan value -> its promotion has ended

    // Without this script the stepper stays hidden (quantity 1) and a total for a plan
    // other than the server's choice hides (input.css); from here on the page is live.
    form.classList.add('is-live');
    [].forEach.call(form.querySelectorAll('.stepper__btn'), function (b) { b.hidden = false; });

    // Operator text arrives as data-asks; Latin runs are isolated with <bdi>, built as
    // DOM nodes so nothing in the attribute can become markup.
    function isolate(el, text) {
      el.textContent = '';
      text.split(LATIN).forEach(function (part, i) {
        if (!part) return;
        if (i % 2) { var b = document.createElement('bdi'); b.lang = 'en'; b.textContent = part; el.appendChild(b); }
        else el.appendChild(document.createTextNode(part));
      });
    }

    function swap(el, text, animate) {
      if (!el || el.textContent === text) return;
      el.textContent = text;
      if (!animate) return;
      el.classList.add('is-from');
      void el.offsetWidth;
      el.classList.remove('is-from');
    }

    function render(animate) {
      var plan = form.querySelector('input[name=plan]:checked');
      if (!plan || !amount) return;
      var max = Number(plan.getAttribute('data-max')) || 1;
      var n = 1;
      if (qty) {
        n = Math.min(Math.max(parseInt(qty.value, 10) || 1, 1), max);
        qty.value = n;
        // Written only when changed: the total is the one thing announced after a change.
        swap(shown, fa(n), false);
        swap(cap, 'حداکثر ' + fa(max) + ' عدد', false);
        if (qtyRow) qtyRow.hidden = max < 2;
        // aria-disabled, not disabled: the button keeps focus at the limit
        [].forEach.call(form.querySelectorAll('.stepper__btn'), function (b) {
          b.setAttribute('aria-disabled', String(Number(b.getAttribute('data-step')) > 0 ? n >= max : n <= 1));
        });
      }
      var question = plan.getAttribute('data-asks') || '';
      if (asksRow) { asksRow.hidden = !question; if (asks.textContent !== question) isolate(asks, question); }
      var set = data[plan.value] && data[plan.value][ended[plan.value] ? 'after' : 'now'];
      var entry = set && set[String(n)];
      if (!entry) return;  // the server built no figure for this pair: show nothing rather than a wrong one
      swap(amount, entry.amount, animate);
      swap(words, entry.words, animate);
      if (cur) cur.hidden = !!entry.free;
      if (words) words.parentNode.hidden = !!entry.free;
    }

    form.addEventListener('change', function () { render(true); });
    [].forEach.call(form.querySelectorAll('.stepper__btn'), function (b) {
      b.addEventListener('click', function () {
        if (b.getAttribute('aria-disabled') === 'true') return;
        qty.value = (parseInt(qty.value, 10) || 1) + Number(b.getAttribute('data-step'));
        render(true);
      });
    });

    // At a plan's promotion end, its server-built post-promotion price and per-month line
    // (template.plan__after) replace the promotional ones, and its total switches to the
    // after-figures. Each plan keeps its own end, so a plan whose discount is still
    // running keeps it.
    function endPlan(input) {
      if (ended[input.value]) return;
      ended[input.value] = true;
      var body = input.nextElementSibling, t = body && body.querySelector('template.plan__after');
      if (t) [].forEach.call(t.content.children, function (node) {
        var old = body.querySelector('.' + node.classList[0]);
        if (old) old.replaceWith(node.cloneNode(true));
      });
      render(true);
    }
    plans.forEach(function (input) {
      if (!input.hasAttribute('data-promo-left')) return;
      var left = Number(input.getAttribute('data-promo-left')) * 1000;
      if (left <= 0) endPlan(input);
      else if (left < 2147483647) setTimeout(function () { endPlan(input); }, left);
    });
    // A deadline badge that runs out ends its own plan, or, the shared one, every plan on offer.
    form.addEventListener('countdown:end', function (e) {
      var own = e.target.closest && e.target.closest('.plan');
      plans.forEach(function (input) {
        if (own ? own.contains(input) : input.hasAttribute('data-promo-left')) endPlan(input);
      });
    });

    // Until S4b the form has no submit button; Enter in a field must not post it either.
    form.addEventListener('submit', function (e) {
      if (form.hasAttribute('data-cart-pending')) e.preventDefault();
    });
    render(false);
  });

  // A stuck invoice taller than the room under the frame anchors by its bottom: its head
  // slides under the header while the total, the button and the terms stay in view. While
  // a plan has focus it sticks by its top instead, so the focused card is never under the
  // header.
  var inv = document.querySelector('.invoice--sticky');
  var frame = document.querySelector('.site-header');
  if (inv && frame) {
    var gap = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--spacing-4')) || 16;
    var place = function () {
      var under = frame.offsetHeight + gap, a = document.activeElement;
      var onPlan = a && inv.contains(a) && a.closest('fieldset');
      inv.style.setProperty('--inv-top', (onPlan ? under : Math.min(under, innerHeight - inv.offsetHeight - gap)) + 'px');
      inv.classList.add('is-sticky');
    };
    place();
    addEventListener('resize', place);
    inv.addEventListener('focusin', place);
    inv.addEventListener('focusout', function () { requestAnimationFrame(place); });
    // Its height changes with the plan, the quantity, the web fonts, a promotion's end and text spacing.
    if ('ResizeObserver' in window) new ResizeObserver(place).observe(inv);
    else inv.addEventListener('change', place);
  }
})();

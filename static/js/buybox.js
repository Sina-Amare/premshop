(function () {
  var form = document.querySelector('.buybox__form');
  if (!form) return;
  var qtyRow = form.querySelector('#qty-row');
  var qty = form.querySelector('#qty');
  var asks = form.querySelector('#asks-hint');
  var selection = form.querySelector('.buybox__selection');
  var submit = form.querySelector('.buybox__cta');
  var cartLink = form.querySelector('.buybox__cart');
  var limit = form.querySelector('#qty-limit');
  // With one option the summary would only repeat it.
  var single = form.querySelectorAll('input[name=plan]').length < 2;
  function currentPlan() { return form.querySelector('input[name=plan]:checked'); }

  function applyPlan(event) {
    var plan = currentPlan();
    if (!plan || plan.disabled) {
      if (submit) { submit.disabled = form.hasAttribute('data-snapshot'); submit.setAttribute('aria-disabled', 'true'); submit.hidden = false; submit.textContent = 'ناموجود'; }
      if (selection) selection.hidden = true;
      if (cartLink) cartLink.hidden = true;
      if (qtyRow && qty) {
        if (qtyRow.contains(document.activeElement) && submit) submit.focus({preventScroll:true});
        qtyRow.hidden = true;
        qty.disabled = true;
      }
      if (limit) limit.hidden = true;
      if (asks) asks.hidden = true;
      return;
    }
    var row = plan.closest('.plan');
    var parts = row.querySelectorAll('[data-plan-price] .price__num, [data-plan-price] .price__cur');
    var title = row.querySelector('.plan__title').textContent;
    var key = title + Array.from(parts).map(function (part) { return part.textContent; }).join('|');
    if (selection && selection.dataset.value !== key) {
      selection.querySelector('[data-selected-title]').textContent = title;
      var price = selection.querySelector('[data-selected-price]');
      price.replaceChildren();
      parts.forEach(function (part) {
        price.appendChild(part.cloneNode(true));
      });
      selection.dataset.value = key;
      selection.hidden = single;
    }
    if (selection && selection.hidden !== single) selection.hidden = single;
    var max = parseInt(plan.getAttribute('data-max'), 10) || 0;
    if (submit && !form.hasAttribute('data-snapshot')) {
      var pending = form.dataset.pending === 'true';
      var full = max < 1 && +plan.dataset.inCart > 0;
      var focused = document.activeElement === submit;
      submit.disabled = false;
      submit.setAttribute('aria-disabled', String(pending || max < 1));
      submit.hidden = full && !pending;
      submit.textContent = max > 0 || full ? 'افزودن به سبد خرید' : 'ناموجود';
      if (cartLink) {
        cartLink.hidden = !full || pending;
        if (focused && submit.hidden) cartLink.focus({preventScroll:true});
      }
    }
    if (qtyRow && qty) {
      qtyRow.hidden = max < 2 && !qtyRow.contains(document.activeElement) && (max === 0 || (parseInt(qty.value, 10) || 1) <= 1);
      qty.disabled = max < 1;
      qty.max = String(Math.max(1, max));
      if (event && event.type === 'change' && parseInt(qty.value, 10) > Math.max(1, max)) qty.value = String(Math.max(1, max));
      form.querySelectorAll('.stepper__btn').forEach(function (button) {
        button.disabled = +button.dataset.step > 0 ? +qty.value >= max : +qty.value <= 1;
      });
      if (limit) {
        var text = max > 0 ? 'از این گزینه تا ' + String(max).replace(/\d/g, function (d) { return '۰۱۲۳۴۵۶۷۸۹'[d]; }) + ' تا می‌تونید بخرید.' : 'بیشتر از این نمی‌تونید از این گزینه بخرید.';
        if (limit.textContent !== text) limit.textContent = text;
        limit.hidden = qtyRow.hidden && max > 0;
      }
    }
    if (asks) {
      var question = plan.getAttribute('data-asks') || '';
      asks.textContent = question ? 'برای فعال‌سازی، ' + question + ' را از شما می‌خواهیم.' : '';
      asks.hidden = !question;
    }
  }

  form.querySelectorAll('input[name=plan]').forEach(function (radio) {
    radio.addEventListener('change', applyPlan);
  });
  document.addEventListener('prices:changed', applyPlan);
  form.addEventListener('cart:availability', applyPlan);
  if (qty) qty.addEventListener('input', applyPlan);
  form.querySelectorAll('.stepper__btn').forEach(function (button) {
    button.addEventListener('click', function () {
      if (!qty) return;
      var next = (parseInt(qty.value, 10) || 1) + parseInt(button.getAttribute('data-step'), 10);
      qty.value = String(Math.min(Math.max(next, 1), parseInt(qty.max, 10) || 1));
      applyPlan();
    });
  });
  applyPlan();
})();

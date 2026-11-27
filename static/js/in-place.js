// A form marked data-in-place sends in the background, and the page takes the server's
// answer into <main> instead of reloading, so the customer keeps their place. The server
// answers exactly as it does a plain post; without this script the form posts as before.
// An answer that cannot be read is never posted again: the page loads its own address
// and shows what the server now holds, so a code is not sent twice.
(function () {
  'use strict';
  if (!window.fetch || !window.DOMParser || !Element.prototype.replaceChildren) return;

  var SAID = '[role=alert], [role=status], .form-error, .form-ok, .field__error';

  function reload() {
    location.replace(location.pathname + location.search);
  }

  function spoken(root) {
    return Array.prototype.find.call(root.querySelectorAll(SAID), function (el) {
      return !el.hidden && el.textContent.trim();
    });
  }

  function swap(html, anchorId, offset) {
    var next = new DOMParser().parseFromString(html, 'text/html');
    var fresh = next.getElementById('main');
    var current = document.getElementById('main');
    if (!fresh || !current) return false;
    // A folded box the customer had open stays open, so what they acted on is still in view.
    var open = Array.prototype.map.call(current.querySelectorAll('details[id][open]'), function (box) {
      return box.id;
    });
    current.replaceChildren.apply(current, Array.prototype.map.call(fresh.childNodes, function (node) {
      return document.importNode(node, true);
    }));
    open.forEach(function (id) {
      var box = document.getElementById(id);
      if (box && box.tagName === 'DETAILS') box.open = true;
    });
    if (next.title) document.title = next.title;
    var anchor = (anchorId && document.getElementById(anchorId)) || current;
    // The part the customer acted on stays where it was on the screen.
    if (offset !== null) window.scrollBy(0, anchor.getBoundingClientRect().top - offset);
    // The answer is said beside the form or, for a refusal, at the top of the page.
    var said = spoken(anchor) || spoken(current);
    var target = said || anchor;
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
    if (said) {
      var box = said.getBoundingClientRect();
      if (box.top < 0 || box.bottom > window.innerHeight) said.scrollIntoView({ block: 'nearest' });
    }
    document.dispatchEvent(new CustomEvent('content:replaced', { detail: { root: current } }));
    return true;
  }

  function settle(response, anchorId, offset) {
    var landed = new URL(response.url, location.href);
    // A page rendered straight from the post (a 400 with the form's error) belongs here;
    // only a redirect to another address sends the customer away, and going there repeats nothing.
    if (response.redirected && (landed.origin !== location.origin || landed.pathname !== location.pathname)) {
      location.assign(response.url);
      return true;
    }
    if ((response.headers.get('content-type') || '').indexOf('text/html') !== 0) return false;
    return response.text().then(function (html) { return swap(html, anchorId, offset); });
  }

  function send(url, init, anchorId, offset) {
    return fetch(url, Object.assign({ credentials: 'same-origin', cache: 'no-store' }, init))
      .then(function (response) { return settle(response, anchorId, offset); })
      .then(function (done) { if (!done) reload(); })
      .catch(reload);
  }

  function place(element) {
    var holder = element && element.parentElement && element.parentElement.closest('[id]');
    return holder ? [holder.id, holder.getBoundingClientRect().top] : [null, null];
  }

  // Safari before 15.4 names no submitter, so the button pressed is remembered from its click.
  var pressed = null;
  document.addEventListener('click', function (event) {
    pressed = event.target.closest
      ? event.target.closest('button:not([type=button]):not([type=reset]), input[type=submit]')
      : null;
  }, true);

  // Capture runs before forms.js marks the button busy and before any other handler.
  document.addEventListener('submit', function (event) {
    var form = event.target;
    // Attributes, not form.action or form.method: a control named "action" shadows the property.
    if (!form.matches('form[data-in-place]') || (form.getAttribute('method') || '').toLowerCase() !== 'post') return;
    var by = event.submitter || (pressed && form.contains(pressed) ? pressed : null);
    pressed = null;
    event.preventDefault();
    if (form.hasAttribute('data-sending')) return;
    form.setAttribute('data-sending', '');
    var body = new FormData(form);
    if (by && by.name) body.append(by.name, by.value);
    var at = place(form);
    send(new URL(form.getAttribute('action') || '', location.href).href, { method: 'POST', body: body }, at[0], at[1]);
  }, true);

  // For a script that already sent its own request and only needs the page brought up to date.
  window.inPlace = {
    refresh: function (url, anchorId) {
      var anchor = anchorId && document.getElementById(anchorId);
      return send(url || location.href, {}, anchor ? anchorId : null,
        anchor ? anchor.getBoundingClientRect().top : null);
    }
  };
})();

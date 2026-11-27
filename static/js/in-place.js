// A form marked data-in-place sends in the background, and the page takes the server's
// answer into <main> instead of reloading, so the customer keeps their place. The server
// answers exactly as it does a plain post; without this script the form posts as before.
// An answer that cannot be read is never posted again: the page reloads and shows what
// the server now holds, so a code is not sent twice.
(function () {
  'use strict';
  if (!window.fetch || !window.DOMParser) return;

  var SAID = '[role=alert], [role=status], .form-error, .form-ok';

  function swap(html, anchorId, offset) {
    var next = new DOMParser().parseFromString(html, 'text/html');
    var fresh = next.getElementById('main');
    var current = document.getElementById('main');
    if (!fresh || !current) return false;
    current.replaceChildren.apply(current, Array.prototype.map.call(fresh.childNodes, function (node) {
      return document.importNode(node, true);
    }));
    if (next.title) document.title = next.title;
    var anchor = (anchorId && document.getElementById(anchorId)) || current;
    // The part the customer acted on stays where it was on the screen.
    if (offset !== null) window.scrollBy(0, anchor.getBoundingClientRect().top - offset);
    var said = Array.prototype.find.call(anchor.querySelectorAll(SAID), function (el) {
      return !el.hidden && el.textContent.trim();
    });
    var target = said || anchor;
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
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
      .then(function (done) { if (!done) location.reload(); })
      .catch(function () { location.reload(); });
  }

  function place(element) {
    var holder = element && element.parentElement && element.parentElement.closest('[id]');
    return holder ? [holder.id, holder.getBoundingClientRect().top] : [null, null];
  }

  // Capture runs before forms.js marks the button busy and before any other handler.
  document.addEventListener('submit', function (event) {
    var form = event.target;
    // Attributes, not form.action or form.method: a control named "action" shadows the property.
    if (!form.matches('form[data-in-place]') || (form.getAttribute('method') || '').toLowerCase() !== 'post') return;
    event.preventDefault();
    if (form.hasAttribute('data-sending')) return;
    form.setAttribute('data-sending', '');
    var body = new FormData(form);
    if (event.submitter && event.submitter.name) body.append(event.submitter.name, event.submitter.value);
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

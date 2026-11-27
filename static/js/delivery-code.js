// The order page's delivery code: the shared six cells over the code field. The
// sixth digit sends it; the row turns green and the page opens the products'
// show buttons, or it turns red and clears. Without this script the plain field
// and its button post as before.
(function () {
  var form = document.getElementById('delivery-code-form');
  if (!form || !window.codeCells) return;
  var errorBox = document.getElementById('delivery-code-error');
  var busy = false;
  var row = window.codeCells(form, {
    onInput: function () { show(''); },
    onComplete: function () { submit(); }
  });
  form.querySelector('[type="submit"]').hidden = true;

  function show(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }
  function submit() {
    if (busy) return;
    busy = true;
    row.lock();
    fetch(form.action, {
      method: 'POST', body: new FormData(form), credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function (r) {
      var isJson = (r.headers.get('content-type') || '').indexOf('application/json') === 0;
      return isJson ? r.json() : Promise.reject(new Error('non-json'));
    }).then(function (data) {
      if (data.ok) {
        row.confirm(function () {
          // On the order page the address differs only by its fragment, which would not reload.
          var samePage = new URL(data.next, location.href).pathname === location.pathname;
          location.replace(data.next);
          if (samePage) location.reload();
        });
        return;
      }
      show(data.error);
      row.fail(function () { row.disable(false); row.focus(); busy = false; });
    }).catch(function () {
      // A 503 page or a dropped connection: the plain post lets the server answer.
      form.submit();
    });
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(); });
})();

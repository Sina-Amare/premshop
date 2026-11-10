// The account page's Telegram section while it waits for the bot's confirm: every two
// seconds the page asks whether the link went through, and reloads once the state has
// changed, linked or expired. Without it, a reload by hand shows the same.
(function () {
  var box = document.querySelector('[data-telegram-wait]');
  if (!box) return;
  var url = box.getAttribute('data-telegram-wait');
  var asking = false;
  var ask = function () {
    if (asking || document.visibilityState !== 'visible') return;
    asking = true;
    fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) { if (data && data.state !== 'waiting') window.location.reload(); })
      .catch(function () {})  // a moment offline: the next tick asks again
      .then(function () { asking = false; });
  };
  window.setInterval(ask, 2000);
  // Back from the Telegram app: ask at once rather than at the next tick.
  document.addEventListener('visibilitychange', ask);
})();

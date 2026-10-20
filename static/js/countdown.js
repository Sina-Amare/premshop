// Amber deadlines. The server writes the badge text (core.formatting.format_countdown:
// days and hours, under a day hours and minutes) with data-left in seconds, and prints
// the fixed end time beside it. The text never ticks: a clock that rewrites itself every
// second is moving content with no way to stop it (WCAG 2.2.2). It is written again
// once when the reader comes back to the tab. At the end the badge hides, its line says
// the promotion is over, countdown:end bubbles, and every rate-sheet price with the same
// end shows the figure the server built for after it (data-after). The end is counted
// from this machine's clock and the seconds the server sent, never from an absolute
// server time, so a visitor whose clock is a day out still sees the right time left.
(function () {
  var FA = '۰۱۲۳۴۵۶۷۸۹', NB = ' ';
  function fa(s) { return String(s).replace(/\d/g, function (d) { return FA[d]; }); }
  function format(sec) {
    var d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    if (d) return fa(d) + NB + 'روز' + (h ? ' و ' + fa(h) + NB + 'ساعت' : '');
    if (h) return fa(h) + NB + 'ساعت' + (m ? ' و ' + fa(m) + NB + 'دقیقه' : '');
    return m ? fa(m) + NB + 'دقیقه' : 'کمتر از یک' + NB + 'دقیقه';
  }
  var start = Date.now();
  function endOf(el) { return start + (Number(el.getAttribute('data-left')) || 0) * 1000; }
  function at(t, fn) {
    var ms = t - Date.now();
    if (ms <= 0) fn();
    else if (ms < 2147483647) setTimeout(fn, ms);  // past setTimeout's 24.8-day limit the page is reloaded first
  }

  var badges = [].map.call(document.querySelectorAll('.countdown[data-left]'), function (el) {
    var end = endOf(el), out = el.querySelector('.countdown__left');
    function write() {
      var t = out && end > Date.now() && format(Math.floor((end - Date.now()) / 1000));
      if (t && out.textContent !== t) out.textContent = t;
    }
    at(end, function () {
      el.classList.add('is-done');
      el.dispatchEvent(new CustomEvent('countdown:end', { bubbles: true }));
      var line = el.closest('.sheet__note, .invoice__promo, .plan__clock, .rs__clock');
      if (line) line.textContent = line.getAttribute('data-over') || 'تخفیف تمام شد.';
    });
    return write;
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) badges.forEach(function (write) { write(); });
  });

  // Rate-sheet prices (the invoice swaps its own): the server's after-figure, no struck
  // price, and a row link that names the price now charged.
  [].forEach.call(document.querySelectorAll('.price[data-left]'), function (price) {
    if (price.closest('form')) return;
    at(endOf(price), function () {
      var num = price.querySelector('.price__num'), was = price.querySelector('.price__was'), k = price.querySelector('.price__now-k');
      var after = price.getAttribute('data-after'), row = price.closest('.rs__plan');
      var link = row && row.querySelector('.rs__plan-link');
      if (link && num) link.setAttribute('aria-label', link.getAttribute('aria-label').replace(num.textContent, after));
      if (num) num.textContent = after;
      if (was) was.remove();
      if (k) k.remove();  // «قیمت با تخفیف» goes with the struck price
    });
  });
})();

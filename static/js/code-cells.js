// Six one-digit cells over a form's single code field: typing, paste, arrow
// and backspace keys, the green row when the code is accepted and the shake
// when it is not. The sign-in page and the checkout's inline sign-in share it;
// each keeps its own submit, errors and countdown.
window.codeCells = function (form, options) {
  var hidden = form.querySelector('input[name="code"]');
  var box = form.querySelector('.otp');
  var cells = Array.prototype.slice.call(box.querySelectorAll('.otp__cell'));
  var FA = '۰۱۲۳۴۵۶۷۸۹';
  var toAscii = function (s) {
    return (s || '').replace(/[۰-۹]/g, function (d) { return String(FA.indexOf(d)); })
                    .replace(/[٠-٩]/g, function (d) { return String('٠١٢٣٤٥٦٧٨٩'.indexOf(d)); });
  };
  // Someone who asked for less motion gets none: the CSS already zeroes every
  // transition, and these timers would otherwise hold the page still for no
  // reason the viewer can see.
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var STEP = still ? 0 : 70;    // between one cell turning green and the next
  var HOLD = still ? 0 : 260;   // the finished row, held, before the page moves

  // Without this script the plain field is the form; with it, the cells are.
  hidden.hidden = true;
  hidden.setAttribute('tabindex', '-1');
  box.hidden = false;
  box.removeAttribute('aria-hidden');

  function value() { return cells.map(function (c) { return c.value; }).join(''); }
  function sync() { hidden.value = value(); }
  function clear() {
    cells.forEach(function (c) { c.value = ''; c.classList.remove('is-filled', 'is-ok', 'is-error'); });
    sync();
  }
  function setState(cls) {
    cells.forEach(function (c) { c.classList.remove('is-ok', 'is-error'); if (cls) c.classList.add(cls); });
  }
  function typed() { if (options.onInput) options.onInput(); setState(null); }
  function fill(digits, from) {
    for (var i = 0; i < digits.length && from + i < cells.length; i++) {
      cells[from + i].value = digits[i];
      cells[from + i].classList.add('is-filled');
    }
    sync();
    cells[Math.min(from + digits.length, cells.length - 1)].focus();
    if (value().length === cells.length) options.onComplete();
  }

  cells.forEach(function (cell, i) {
    cell.addEventListener('input', function () {
      var digits = toAscii(cell.value).replace(/\D/g, '');
      cell.value = '';
      if (!digits) { cell.classList.remove('is-filled'); sync(); return; }
      typed();
      fill(digits, i);                         // handles a single key and an autofill burst alike
    });
    cell.addEventListener('keydown', function (e) {
      if (e.key === 'Backspace' && !cell.value && i > 0) {
        cells[i - 1].value = '';
        cells[i - 1].classList.remove('is-filled');
        cells[i - 1].focus();
        sync();
        e.preventDefault();
      } else if (e.key === 'ArrowLeft' && i > 0) { cells[i - 1].focus(); e.preventDefault(); }
      else if (e.key === 'ArrowRight' && i < cells.length - 1) { cells[i + 1].focus(); e.preventDefault(); }
    });
    cell.addEventListener('focus', function () { cell.select(); });
    cell.addEventListener('paste', function (e) {
      // Gmail → copy → paste is the main path. Take the digits, wherever the
      // cursor happened to be, and always start from the first cell.
      var text = (e.clipboardData || window.clipboardData).getData('text');
      var digits = toAscii(text).replace(/\D/g, '').slice(0, cells.length);
      if (digits) { e.preventDefault(); typed(); clear(); fill(digits, 0); }
    });
  });

  return {
    cells: cells,
    clear: clear,
    focus: function () { cells[0].focus(); },
    disable: function (off) { cells.forEach(function (c) { c.disabled = off; }); },
    // While the code is checked: no cursor, and no stale selection under the green.
    lock: function () {
      sync();
      cells.forEach(function (c) { try { c.setSelectionRange(0, 0); } catch (e) {} c.blur(); c.disabled = true; });
    },
    // Accepted: one digit at a time, left to right, the way a person reads them back.
    confirm: function (done) {
      cells.forEach(function (c, i) {
        c.classList.remove('is-error');
        setTimeout(function () { c.classList.add('is-ok'); }, i * STEP);
      });
      setTimeout(done, (cells.length - 1) * STEP + HOLD);
    },
    // Wrong is one event, not six: the whole row turns at once and shakes once.
    fail: function (done) {
      setState('is-error');
      box.classList.add('is-shaking');
      setTimeout(function () {
        box.classList.remove('is-shaking');
        clear();
        done();
      }, still ? 0 : 380);
    }
  };
};

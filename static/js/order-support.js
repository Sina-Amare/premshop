(function () {
  'use strict';
  // Each copy button names its status line (data-copy-note), what to select when
  // the browser refuses the clipboard (data-copy-select) and what to say then.
  function start(root) {
    root.querySelectorAll('[data-copy-text]').forEach(function (button) {
      var fallback = document.getElementById(button.dataset.copySelect);
      var status = document.getElementById(button.dataset.copyNote);
      var label = button.textContent;
      var timer;
      button.hidden = false;
      button.addEventListener('click', async function () {
        try {
          await navigator.clipboard.writeText(button.dataset.copyText);
          // The button itself confirms; the live region still tells a screen reader.
          status.classList.add('sr-only');
          status.textContent = 'کپی شد';
          button.textContent = 'کپی شد';
          button.classList.add('is-copied');
          clearTimeout(timer);
          timer = setTimeout(function () {
            button.textContent = label;
            button.classList.remove('is-copied');
          }, 2200);
        } catch (_) {
          // A refusal right after a success must not leave «کپی شد» on the button.
          clearTimeout(timer);
          button.textContent = label;
          button.classList.remove('is-copied');
          fallback.hidden = false;
          var selection = window.getSelection();
          var range = document.createRange();
          range.selectNodeContents(fallback);
          selection.removeAllRanges();
          selection.addRange(range);
          status.classList.remove('sr-only');
          status.textContent = button.dataset.copyFailed;
        }
      });
    });
  }
  start(document);
  document.addEventListener('content:replaced', function (event) { start(event.detail.root); });
}());

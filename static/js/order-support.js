(function () {
  'use strict';
  var button = document.querySelector('[data-copy-support]');
  if (!button) return;
  var identifier = document.getElementById('support-identifier');
  var status = document.querySelector('[data-copy-status]');
  button.hidden = false;
  button.addEventListener('click', async function () {
    try {
      // The order number travels with the identifier, so one paste tells support both.
      await navigator.clipboard.writeText(button.dataset.copyText);
      status.textContent = 'کپی شد';
    } catch (_) {
      var selection = window.getSelection();
      var range = document.createRange();
      range.selectNodeContents(identifier);
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'کپی انجام نشد؛ شناسه را انتخاب و کپی کنید.';
    }
  });
}());

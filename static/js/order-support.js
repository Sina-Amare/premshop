(function () {
  'use strict';
  var button = document.querySelector('[data-copy-support]');
  if (!button) return;
  var identifier = document.getElementById('support-identifier');
  var status = document.querySelector('[data-copy-status]');
  button.hidden = false;
  button.addEventListener('click', async function () {
    try {
      await navigator.clipboard.writeText(identifier.textContent.trim());
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

(function () {
  'use strict';
  var button = document.querySelector('[data-copy-support]');
  if (!button) return;
  var identifier = document.getElementById('support-identifier');
  var status = document.querySelector('[data-copy-status]');
  var label = button.textContent;
  var timer;
  button.hidden = false;
  button.addEventListener('click', async function () {
    try {
      // The order number travels with the identifier, so one paste tells support both.
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
      var selection = window.getSelection();
      var range = document.createRange();
      range.selectNodeContents(identifier);
      selection.removeAllRanges();
      selection.addRange(range);
      status.classList.remove('sr-only');
      status.textContent = 'کپی خودکار انجام نشد و می‌توانید شناسه‌ی پشتیبانی را خودتان انتخاب و کپی کنید.';
    }
  });
}());

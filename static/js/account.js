(() => {
  // Signing out asks once in a dialog; the dialog's own form does the signing out.
  const form = document.querySelector('[data-logout]');
  const dialog = document.getElementById('logout-dialog');
  if (!form || !dialog || typeof dialog.showModal !== 'function') return;
  form.addEventListener('submit', event => {
    event.preventDefault();
    dialog.showModal();
  });
  dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
  // A click on the dimmed backdrop lands on the dialog element itself.
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
})();

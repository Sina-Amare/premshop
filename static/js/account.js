(() => {
  // Signing out asks once in a dialog; the dialog's own form does the signing out.
  const start = () => {
    const form = document.querySelector('[data-logout]:not([data-dialog-ready])');
    const dialog = document.getElementById('logout-dialog');
    if (!form || !dialog || typeof dialog.showModal !== 'function') return;
    form.setAttribute('data-dialog-ready', '');
    form.addEventListener('submit', event => {
      event.preventDefault();
      dialog.showModal();
    });
    dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
    // A click on the dimmed backdrop lands on the dialog element itself.
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  };
  start();
  // A form answered in place replaces the page's main part, the sign-out with it.
  document.addEventListener('content:replaced', start);
})();

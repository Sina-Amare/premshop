// A form that takes seconds (the code request rides a real SMTP round trip) must
// look busy, or the customer clicks again and burns a second code. The label
// comes from data-busy so each form says what it is actually doing.
document.querySelectorAll('form').forEach(function (form) {
  form.addEventListener('submit', function () {
    var button = form.querySelector('button[type="submit"][data-busy]');
    if (!button) return;
    button.disabled = true;
    button.textContent = button.getAttribute('data-busy');
  });
});

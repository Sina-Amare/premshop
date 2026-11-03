// Progressive enhancement: every password field gains a reveal toggle. Done here
// rather than in each form so the next step's forms inherit it, and so a field
// rendered by Django's own auth forms gets it too without subclassing anything.
// With JS off the field still works — it is just always masked, which is the
// behaviour before this existed.
(function () {
  var EYE = '<svg data-eye-on viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 12S5 5.5 12 5.5 22.5 12 22.5 12 19 18.5 12 18.5 1.5 12 1.5 12Z"/><circle cx="12" cy="12" r="3.2"/></svg>'
           + '<svg data-eye-off viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 6.2A8.9 8.9 0 0 1 12 6c7 0 10.5 6 10.5 6a17 17 0 0 1-3.5 4.1"/><path d="M6.4 7.6A16.8 16.8 0 0 0 1.5 12S5 18 12 18a9.6 9.6 0 0 0 3.5-.65"/><path d="M9.9 9.9a3.2 3.2 0 0 0 4.3 4.3"/></svg>';
  document.querySelectorAll('input[type="password"]').forEach(function (input) {
    if (input.parentElement.classList.contains('field__wrap')) return;
    var wrap = document.createElement('span');
    wrap.className = 'field__wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var button = document.createElement('button');
    button.type = 'button';                 // never submit the form
    button.className = 'field__eye';
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', 'نمایش رمز عبور');
    button.innerHTML = EYE;
    button.addEventListener('click', function () {
      var shown = input.type === 'text';
      input.type = shown ? 'password' : 'text';
      button.setAttribute('aria-pressed', String(!shown));
      button.setAttribute('aria-label', shown ? 'نمایش رمز عبور' : 'پنهان کردن رمز عبور');
      input.focus();
    });
    wrap.appendChild(button);
  });
})();
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

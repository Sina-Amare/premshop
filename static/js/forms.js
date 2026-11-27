// A form that takes seconds (the code request waits on the SMS provider) must
// look busy, or the customer clicks again and burns a second code. The label
// comes from data-busy so each form says what it is actually doing. One listener
// on the document also covers a form that in-place.js has just put on the page.
document.addEventListener('submit', function (event) {
  var button = event.target.querySelector('button[type="submit"][data-busy]');
  if (!button) return;
  button.disabled = true;
  button.textContent = button.getAttribute('data-busy');
});

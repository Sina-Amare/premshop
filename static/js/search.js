// Suggestions under the header search while the buyer types. Without this file
// the field is the plain search box it always was, and the list stays hidden.
(() => {
  const list = document.getElementById('search-suggest');
  const form = list?.closest('form');
  const input = form?.querySelector('input[name=q]');
  if (!input) return;
  let timer, request, active = -1;
  const options = () => [...list.children];

  const highlight = index => {
    active = index;
    options().forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
    if (index < 0) input.removeAttribute('aria-activedescendant');
    else input.setAttribute('aria-activedescendant', options()[index].id);
  };
  const toggle = open => {
    list.hidden = !open;
    input.setAttribute('aria-expanded', String(open));
    if (!open) highlight(-1);
  };
  // Also drops the pause and the request in flight, or their answer reopens the list.
  const close = () => {
    clearTimeout(timer);
    request?.abort();
    toggle(false);
  };
  const show = results => {
    // Each row is the server's autoescaped template, the only markup this inserts.
    list.innerHTML = results.map(item => item.html).join('');
    options().forEach((option, i) => {
      option.id = `${list.id}-${i}`;
      option.dataset.url = results[i].url;
    });
    highlight(-1);
    toggle(results.length > 0);
  };
  const suggest = async () => {
    const query = input.value.trim();
    request?.abort();
    if (query.length < 2) return show([]);
    request = new AbortController();
    try {
      const response = await fetch(`${list.dataset.endpoint}?q=${encodeURIComponent(query)}`, {signal: request.signal});
      const results = response.ok ? (await response.json()).results : [];
      // An answer that lands after more typing is for a question no longer asked.
      if (query === input.value.trim()) show(results);
    } catch (error) {
      if (error.name !== 'AbortError') show([]);
    }
  };

  // Set here, not in the template: a combobox that cannot open is a lie to a screen reader.
  for (const [name, value] of Object.entries({
    role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': list.id, autocomplete: 'off',
  })) input.setAttribute(name, value);

  input.addEventListener('input', () => {
    highlight(-1);
    clearTimeout(timer);
    timer = setTimeout(suggest, 200);
  });
  input.addEventListener('keydown', event => {
    const count = list.children.length;
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && count) {
      event.preventDefault();
      toggle(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      highlight(active < 0 ? (step > 0 ? 0 : count - 1) : (active + step + count) % count);
    } else if (event.key === 'Enter' && !list.hidden && active >= 0) {
      event.preventDefault();
      location.assign(options()[active].dataset.url);
    } else if (event.key === 'Escape') {
      // Without this a search field also empties itself on Escape.
      if (!list.hidden) event.preventDefault();
      close();
    }
  });
  // Pressing an option must not move focus off the field, or the list closes under the pointer.
  list.addEventListener('mousedown', event => event.preventDefault());
  list.addEventListener('click', event => {
    const option = event.target.closest('[role=option]');
    if (option) location.assign(option.dataset.url);
  });
  document.addEventListener('pointerdown', event => { if (!form.contains(event.target)) close(); });
  form.addEventListener('focusout', event => { if (!form.contains(event.relatedTarget)) close(); });
  // Closed before the page is left, so Back does not restore it open over the page.
  addEventListener('pagehide', close);
})();

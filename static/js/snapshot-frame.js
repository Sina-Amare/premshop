(() => {
  if (parent === window) return;
  const parentOrigin = new URL(document.baseURI).origin;
  let modal = false;
  // scrollIntoView also moves the enclosing editor when the preview is inline.
  const reveal = target => {
    if (!target) return;
    const box = target.getBoundingClientRect();
    const header = document.querySelector('.v-header');
    const edge = header?.contains(target) ? 0 : (header?.getBoundingClientRect().height || 0) + 16;
    window.scrollTo({top:scrollY + box.top - edge,behavior:'instant'});
  };
  const clear = () => {
    document.querySelectorAll('.snapshot-highlight').forEach(e => e.classList.remove('snapshot-highlight'));
    for (const placeholder of document.querySelectorAll('.snapshot-placeholder')) {
      if (placeholder.hasAttribute('data-preview-empty')) {placeholder.hidden=true;placeholder.replaceChildren();placeholder.classList.remove('snapshot-placeholder');}
      else placeholder.remove();
    }
    for (const container of document.querySelectorAll('[data-preview-revealed]')) {container.hidden=true;container.removeAttribute('data-preview-revealed');}
  };
  document.addEventListener('keydown', event => {
    if (document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') {event.preventDefault();parent.postMessage({type:'snapshot-escape'},parentOrigin);}
    if (event.key !== 'Tab' || !modal) return;
    const controls=[...document.querySelectorAll('a[href],button,input,summary,[tabindex]')].filter(e=>!e.disabled && e.tabIndex>=0 && e.getClientRects().length);
    if ((event.shiftKey && document.activeElement===controls[0]) || (!event.shiftKey && document.activeElement===controls.at(-1))) {
      event.preventDefault();parent.postMessage({type:'snapshot-tab',backward:event.shiftKey},parentOrigin);
    }
  });
  document.addEventListener('submit', event => event.preventDefault(), true);
  document.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    event.preventDefault();
    if (link.hash) reveal(document.querySelector(link.hash));
  }, true);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== parentOrigin) return;
    if (event.data?.type === 'snapshot-modal') {modal=event.data.modal;return;}
    if (event.data?.type === 'snapshot-clear') {clear();return;}
    if (event.data?.type !== 'snapshot-target') return;
    const {key,row,field,label,empty} = event.data;
    clear();
    const root = document.querySelector(`[data-preview-target="${CSS.escape(key)}"],#${CSS.escape(key)}`);
    if (!root) {parent.postMessage({type:'snapshot-missing'},parentOrigin);return;}
    const rowSelector = row ? `[data-preview-row="${CSS.escape(row)}"]` : null;
    const plan = rowSelector && document.querySelector(`.plans ${rowSelector} input[name=plan]`);
    const unavailablePlan = row && ['quantity','customer-input'].includes(key) && (!plan || plan.disabled);
    if (plan && !plan.disabled) plan.click();
    const rowTarget = rowSelector && (root.matches(rowSelector) ? root : root.querySelector(rowSelector));
    const needsRow = row && (['plans','specs','sections','questions'].includes(key) || (key==='header' && row.startsWith('specs-')));
    let target = rowTarget || root;
    const exact = field && `[data-preview-field~="${CSS.escape(field)}"]`;
    if (exact) target = target.matches(exact) ? target : target.querySelector(exact) || target;
    if (needsRow && !rowTarget) {
      target = document.createElement(key==='specs' ? 'tr' : 'div');
      target.dataset.previewField = field;
      if (key==='specs') root.querySelector('tbody').append(target);
      else if (key==='questions') root.querySelector('dl').insertBefore(target,root.querySelector('#delivery'));
      else if (key==='sections') root.insertBefore(target,root.querySelector('#before-buying'));
      else (key==='header' ? root.querySelector('.pdp__meta') : root).append(target);
      target.dataset.previewGenerated = '';
    }
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor.hasAttribute('data-preview-container') && ancestor.hidden) {ancestor.hidden=false;ancestor.dataset.previewRevealed='';}
    }
    if (target.hasAttribute('data-preview-empty') || target.hasAttribute('data-preview-generated') || !target.getBoundingClientRect().height || unavailablePlan) {
      if (!target.hasAttribute('data-preview-empty') && !target.hasAttribute('data-preview-generated')) {
        const placeholder=document.createElement('div');target.after(placeholder);target=placeholder;
      }
      if (!target.hasAttribute('data-preview-empty')) target.dataset.previewField=field;
      const definition = target.parentElement.tagName==='DL';
      const heading=document.createElement(definition?'dt':'strong');heading.textContent=label;
      const note=document.createElement(definition?'dd':'span');note.textContent=empty;
      if (target.tagName==='TR') {const cell=document.createElement('td');cell.colSpan=2;cell.append(heading,note);target.append(cell);}
      else target.replaceChildren(heading,note);
      target.hidden=false;target.classList.add('snapshot-placeholder');
    }
    target.classList.add('snapshot-highlight');
    reveal(target);
  });
})();

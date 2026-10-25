(() => {
  if (parent === window) return;
  const parentOrigin = new URL(document.baseURI).origin;
  let modal = false;
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
    if (link.hash) document.querySelector(link.hash)?.scrollIntoView({block:'center'});
  }, true);
  window.addEventListener('message', event => {
    if (event.source !== parent || event.origin !== parentOrigin) return;
    if (event.data?.type === 'snapshot-modal') {modal=event.data.modal;return;}
    if (event.data?.type !== 'snapshot-target') return;
    const key = event.data.key;
    const row = event.data.row;
    const target = (row && document.querySelector(`[data-preview-row="${CSS.escape(row)}"]`)) || document.querySelector(`[data-preview-target="${CSS.escape(key)}"],#${CSS.escape(key)}`);
    document.querySelectorAll('.snapshot-highlight').forEach(e => e.classList.remove('snapshot-highlight'));
    if (!target) {parent.postMessage({type:'snapshot-missing'},parentOrigin);return;}
    for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) if (ancestor.tagName === 'DETAILS') ancestor.open = true;
    const plan=target.querySelector('input[name=plan]:not(:disabled)');if (plan) plan.click();
    target.classList.add('snapshot-highlight');
    target.scrollIntoView({block:'center',behavior:'instant'});
  });
})();

document.addEventListener('DOMContentLoaded', () => {
  const jumps = document.querySelector('.panel-section-jumps');
  if (!jumps) return;
  const sections = [...document.querySelectorAll('#product_form > .flex > fieldset, #product_form [id$="-group"]')];
  for (const [index, section] of sections.entries()) {
    const heading = section.querySelector('h2, h3, legend, summary');
    if (!heading) continue;
    section.id ||= `product-section-${index}`;
    section.tabIndex = -1;
    const link = document.createElement('a');
    link.href = `#${section.id}`;
    link.textContent = heading.textContent.trim();
    link.addEventListener('click', () => {
      const details = section.matches('details') ? section : section.querySelector('details');
      if (details) details.open = true;
    });
    jumps.append(link);
  }
  jumps.hidden = !jumps.childElementCount;
});


document.addEventListener('DOMContentLoaded', () => {
  const panel = document.querySelector('.snapshot-panel');
  if (!panel) return;
  const form = document.querySelector('#content-main > form');
  form.before(panel);
  const frame = panel.querySelector('iframe');
  const status = panel.querySelector('[data-snapshot-status]');
  const errors = panel.querySelector('[data-snapshot-errors]');
  const placements = JSON.parse(document.getElementById('field-placements').textContent);
  const text = JSON.parse(document.getElementById('snapshot-text').textContent);
  const processed = new WeakSet();
  const expand = panel.querySelector('[data-snapshot-expand]');
  const narrow = matchMedia('(max-width:1023px)');
  let revision = 0, renderedRevision = -1, sequence = 0, target = null, opener = null, ready = false;
  const stale = () => {revision++;if (ready) status.textContent = text.stale;};
  const modal = () => {
    const full = panel.classList.contains('is-expanded');
    panel.setAttribute('role', full ? 'dialog' : 'region');
    if (full) panel.setAttribute('aria-modal','true');else panel.removeAttribute('aria-modal');
    document.body.classList.toggle('snapshot-modal', !panel.hidden && full);
    if (!panel.hidden && full && !panel.contains(document.activeElement)) panel.querySelector('[data-snapshot-close]').focus();
    frame.contentWindow?.postMessage({type:'snapshot-modal',modal:!panel.hidden && full},location.origin);
    const content = document.getElementById('content-main');
    for (const child of content.children) if (child !== panel) child.inert = !panel.hidden && full;
    for (const selector of ['#nav-sidebar','#header','#submit-row']) {
      const element = document.querySelector(selector);if (element) element.inert = !panel.hidden && full;
    }
  };
  const highlight = () => {
    if (!target) return;
    const deleted = target.row && form.elements.namedItem(`${target.row}-DELETE`)?.checked;
    frame.contentWindow.postMessage({type:'snapshot-target',...target,empty:deleted ? text.deleted : text.empty},location.origin);
  };
  const refresh = async () => {
    const request = ++sequence, submitted = revision;
    status.textContent = text.busy;
    panel.setAttribute('aria-busy','true');
    frame.contentWindow?.postMessage({type:'snapshot-clear'},location.origin);
    try {
      const response = await fetch(panel.dataset.snapshotUrl, {method:'POST',body:new FormData(form),credentials:'same-origin',headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value}});
      const html = await response.text();
      if (request !== sequence) return;
      if (response.status === 422) {errors.innerHTML = html;status.textContent = text.failed;return;}
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html') || response.redirected) throw new Error('Preview unavailable');
      errors.replaceChildren();
      frame.srcdoc = html;
      ready = true;
      renderedRevision = submitted;
      status.textContent = submitted === revision ? text.fresh : text.stale;
    } catch {
      if (request === sequence) status.textContent = text.failed;
    } finally {
      if (request === sequence) panel.removeAttribute('aria-busy');
    }
  };
  const open = (button, destination = null) => {
    opener = button;target = destination;
    panel.hidden = false;
    document.body.classList.add('snapshot-open');
    modal();
    panel.querySelector('[data-snapshot-close]').focus({preventScroll:true});
    if (narrow.matches) panel.scrollIntoView({block:'start'});
    if (!ready || (destination && renderedRevision !== revision)) refresh();else highlight();
  };
  const close = () => {
    panel.hidden = true;modal();document.body.classList.remove('snapshot-open');opener?.focus();
  };
  frame.addEventListener('load', () => {modal();highlight();});
  document.querySelector('[data-snapshot-open]').addEventListener('click', event => open(event.currentTarget));
  panel.querySelector('[data-snapshot-refresh]').addEventListener('click', refresh);
  panel.querySelector('[data-snapshot-close]').addEventListener('click', close);
  expand.addEventListener('click', () => {panel.classList.toggle('is-expanded');expand.setAttribute('aria-pressed',String(panel.classList.contains('is-expanded')));modal();});
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') {event.preventDefault();close();}
    if (event.key !== 'Tab' || panel.getAttribute('aria-modal') !== 'true') return;
    const focusable = [...panel.querySelectorAll('button,a,iframe')].filter(e=>!e.hidden && !e.disabled);
    if (event.shiftKey && document.activeElement === focusable[0]) {event.preventDefault();focusable.at(-1).focus();}
    if (!event.shiftKey && document.activeElement === focusable.at(-1)) {event.preventDefault();focusable[0].focus();}
  });
  errors.addEventListener('click', event => {
    const link = event.target.closest('[data-field]');if (!link) return;
    event.preventDefault();close();
    const field = document.getElementById(link.dataset.field);
    if (field) {for (let ancestor = field.parentElement;ancestor;ancestor=ancestor.parentElement) if (ancestor.tagName==='DETAILS') ancestor.open=true;field.scrollIntoView({block:'center'});if (field.tabIndex<0) field.tabIndex=-1;field.focus();}
  });
  window.addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== location.origin) return;
    if (event.data?.type === 'snapshot-missing') status.textContent = text.absent;
    if (event.data?.type === 'snapshot-escape') close();
    if (event.data?.type === 'snapshot-tab' && panel.getAttribute('aria-modal') === 'true') {
      const buttons=[...panel.querySelectorAll('header button')];
      (event.data.backward ? buttons.at(-1) : buttons[0]).focus();
    }
  });
  const guide = () => {
    const specs = form.querySelector('#specs-group');
    if (specs && !specs.querySelector('#spec-placement-note')) {
      const note=document.createElement('p');note.id='spec-placement-note';note.className='field-placement';
      note.textContent=placements['specs.title'][1]+' · '+placements['specs.is_key'][1];specs.prepend(note);
    }
    for (const control of form.querySelectorAll('input[name],textarea[name],select[name]')) {
      if (processed.has(control) || control.name.includes('__prefix__') || control.type === 'hidden') continue;
      const match = /^(plans|specs|sections|questions)-(\d+)-(.+?)(?:_[01])?$/.exec(control.name);
      const model = form.id.replace('_form','');
      const field = match ? `${match[1]}.${match[3]}` : `${model}.${control.name.replace(/_[01]$/,'')}`;
      const placement = placements[field];
      if (!placement) continue;
      processed.add(control);
      if (match?.[1] === 'specs') {
        control.setAttribute('aria-describedby',`${control.getAttribute('aria-describedby') || ''} spec-placement-note`.trim());
      }
      const line=control.closest('.field-line');
      const fieldContainer = line?.querySelector(':scope > .grow') || control.closest('td,.field-box') || control.parentElement;
      if ([...fieldContainer.querySelectorAll(':scope > .field-placement')].some(note => note.dataset.placementField === field)) continue;
      const note = document.createElement('div');note.className='field-placement';
      note.dataset.placementField = field;
      if (match?.[1] !== 'specs') {const explanation = document.createElement('p');explanation.textContent=placement[1];note.append(explanation);}
      if (placement[0]) {
        const label = control.labels?.[0]?.textContent.trim() || control.closest('table')?.querySelectorAll('thead th')[control.closest('td')?.cellIndex]?.textContent.trim() || placement[1];
        const button = document.createElement('button');button.type='button';button.textContent=text.open;button.dataset.snapshotField=control.name;button.setAttribute('aria-label',`${text.open}: ${label}`);
        button.addEventListener('click',()=>open(button,{key:placement[0],field:match ? match[3] : control.name.replace(/_[01]$/,''),label,row:match ? `${match[1]}-${match[2]}` : model==='plan'?'plan':null}));
        note.append(button);
      }
      fieldContainer.append(note);
    }
  };
  form.addEventListener('input',stale);form.addEventListener('change',stale);
  let pending = false;
  new MutationObserver(mutations=>{
    if (mutations.every(m=>[...m.addedNodes,...m.removedNodes].every(n=>n.nodeType===3 || n.classList?.contains('field-placement')))) return;
    if (pending) return;
    pending=true;queueMicrotask(()=>{pending=false;guide();stale();});
  }).observe(form,{childList:true,subtree:true});
  guide();
  const colour = form.querySelector('[name=brand_colour]');
  const art = form.querySelector('[data-brand-preview]');
  if (colour && art) {
    const picker = art.querySelector('[data-brand-picker]'), error = art.querySelector('[data-brand-error]');
    const update = () => {
      const value = colour.value.trim();
      const valid = !value || /^#[0-9a-f]{6}$/i.test(value);
      error.hidden = valid;colour.setAttribute('aria-invalid',String(!valid));
      if (valid) {art.style.setProperty('--brand',value || '#737373');picker.value=value || '#737373';}
    };
    colour.addEventListener('input',update);
    picker.addEventListener('input',()=>{colour.value=picker.value;colour.dispatchEvent(new Event('input',{bubbles:true}));colour.dispatchEvent(new Event('change',{bubbles:true}));});
    art.querySelector('[data-brand-reset]').addEventListener('click',()=>{colour.value='';colour.dispatchEvent(new Event('input',{bubbles:true}));colour.dispatchEvent(new Event('change',{bubbles:true}));});
    const image = form.querySelector('[name=image]'), clear = form.querySelector('[name="image-clear"]');
    const canvas = art.querySelector('.pcard__art');
    const savedArt = canvas.querySelector('img,.pcard__mono')?.cloneNode(true);
    let blob;
    const updateImage = () => {
      if (blob) { URL.revokeObjectURL(blob); blob = undefined; }
      const file = image?.files[0];
      let content;
      if (file && /^image\/(png|jpeg|webp)$/.test(file.type)) {
        content = document.createElement('img');content.alt = '';
        blob = URL.createObjectURL(file);content.src = blob;
      } else if (!clear?.checked && savedArt) content = savedArt.cloneNode(true);
      else {
        content = document.createElement('span');content.className = 'pcard__mono';
        content.textContent = form.querySelector('[name=name]')?.value.slice(0,1) || '';
      }
      canvas.querySelector('img,.pcard__mono')?.remove();
      canvas.prepend(content);
    };
    image?.addEventListener('change',updateImage);
    clear?.addEventListener('change',updateImage);
    update();
  }
});

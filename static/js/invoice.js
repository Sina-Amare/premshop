(() => {
  const button = document.querySelector('[data-invoice-download]');
  const status = document.querySelector('[data-invoice-status]');
  const payload = document.getElementById('invoice-data');
  if (!button || !status || !payload || !document.fonts) return;
  button.hidden = false;
  button.addEventListener('click', async () => {
    button.disabled = true;
    status.textContent = 'در حال آماده‌سازی تصویر فاکتور…';
    try {
      const faces = await document.fonts.load('400 26px Vazirmatn');
      await document.fonts.ready;
      if (!faces.length || !document.fonts.check('400 26px Vazirmatn')) throw new Error('Font unavailable');
      const data = JSON.parse(payload.textContent);
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas unavailable');
      const width = 1200;
      const margin = 72;
      const available = width - margin * 2;
      const rows = [];
      let y = 72;
      function add(text, size = 28, weight = 400, space = 12) {
        context.font = `${weight} ${size}px Vazirmatn`;
        let line = '';
        for (const word of String(text).split(/\s+/)) {
          const candidate = line ? `${line} ${word}` : word;
          if (context.measureText(candidate).width <= available) {
            line = candidate;
            continue;
          }
          if (line) {
            rows.push({text: line, y, size, weight});
            y += size * 1.8;
            line = '';
          }
          for (const {segment} of new Intl.Segmenter('fa', {granularity: 'grapheme'}).segment(word)) {
            if (line && context.measureText(line + segment).width > available) {
              rows.push({text: line, y, size, weight});
              y += size * 1.8;
              line = '';
            }
            line += segment;
          }
        }
        if (line) {
          rows.push({text: line, y, size, weight});
          y += size * 1.8;
        }
        y += space;
      }
      add('PremShop', 28);
      add('فاکتور سفارش', 38);
      add(`شماره‌ی سفارش: ${data.number}`);
      add(`تاریخ ثبت: ${data.date}`);
      add(`وضعیت: ${data.status}`);
      add(data.notice, 26, 400, 32);
      for (const line of data.lines) {
        add(line.name, 30);
        add(line.plan, 26);
        add(`وضعیت: ${line.status}`, 24);
        add(`تعداد: ${line.quantity}    قیمت هر عدد: ${line.price}`, 26);
        add(`جمع این محصول: ${line.amount}`, 26, 400, 30);
      }
      add(`جمع محصولات: ${data.subtotal}`);
      add(`تخفیف: ${data.discount}`);
      add(`مبلغ نهایی سفارش: ${data.total}`, 32);
      add(data.notice, 24);
      const height = Math.ceil(y + margin);
      // Bound pixel memory while retaining every row in long orders.
      const scale = Math.min(1, Math.sqrt(16000000 / (width * height)), 30000 / height);
      canvas.width = Math.ceil(width * scale);
      canvas.height = Math.ceil(height * scale);
      context.scale(scale, scale);
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, width, height);
      context.fillStyle = '#131313';
      context.direction = 'rtl';
      context.textAlign = 'right';
      context.textBaseline = 'top';
      for (const row of rows) {
        context.font = `${row.weight} ${row.size}px Vazirmatn`;
        context.fillText(row.text, width - margin, row.y);
      }
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('Image unavailable');
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `PremShop-invoice-${data.number}.png`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      status.textContent = 'تصویر فاکتور آماده شد.';
    } catch (_) {
      status.textContent = 'ساخت تصویر فاکتور ممکن نشد. دوباره امتحان کنید؛ متن فاکتور در همین صفحه در دسترس است.';
    } finally {
      button.disabled = false;
    }
  });
})();

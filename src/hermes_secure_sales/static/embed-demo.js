(() => {
  const $ = (selector) => document.querySelector(selector);
  const frame = $('#preview-frame');
  function code() {
    const slug = $('#shop-slug').value.trim();
    const src = `${location.origin}/embed/${encodeURIComponent(slug)}`;
    return `<iframe src="${src}" title="Trò chuyện với shop" loading="lazy" referrerpolicy="strict-origin" style="width:100%;height:680px;border:0;border-radius:16px" allow="clipboard-write"></iframe>`;
  }
  function update() { frame.src = `/embed/${encodeURIComponent($('#shop-slug').value.trim())}`; $('#snippet').textContent = code(); $('#copy-status').textContent = ''; }
  $('#load-preview').addEventListener('click', update);
  $('#shop-slug').addEventListener('keydown', (event) => { if (event.key === 'Enter') update(); });
  $('#mobile-toggle').addEventListener('click', () => { frame.classList.toggle('mobile'); $('#mobile-toggle').textContent = frame.classList.contains('mobile') ? 'Xem kích thước lớn' : 'Xem trên điện thoại'; });
  $('#copy-snippet').addEventListener('click', async () => { try { await navigator.clipboard.writeText(code()); $('#copy-status').textContent = 'Đã sao chép mã nhúng.'; $('#copy-status').classList.add('ok'); } catch { $('#copy-status').textContent = 'Trình duyệt chưa cho phép sao chép. Hãy sao chép mã hiển thị ở trên.'; } });
  update();
})();

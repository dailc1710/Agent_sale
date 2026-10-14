(() => {
  const $ = (selector) => document.querySelector(selector);
  const path = location.pathname.split('/').filter(Boolean);
  const embedded = path[0] === 'embed';
  const slug = decodeURIComponent(path[1] || '');
  const storageKey = `hermes.public-session.${slug}`;
  const parentOrigin = embedded && document.referrer ? (() => {
    try { return new URL(document.referrer).origin; } catch { return ''; }
  })() : '';
  let shop = null;
  let token = sessionStorage.getItem(storageKey) || '';
  let busy = false;
  let awaitingApproval = false;
  let historyPollTimer = null;
  let pendingRequestId = '';
  let pendingText = '';
  let consultationBusy = false;

  function headers(extra = {}) {
    const values = { ...extra };
    if (token) values.Authorization = `Bearer ${token}`;
    if (embedded && parentOrigin) values['X-Embed-Origin'] = parentOrigin;
    return values;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: headers({ ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.detail || 'Chatbot đang bận. Vui lòng thử lại.');
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function showError(text) {
    const node = $('#chat-error');
    node.textContent = text;
    node.hidden = !text;
  }

  function addBubble(role, body, author = '') {
    const node = document.createElement('div');
    node.className = `chat-bubble ${role}`;
    if (author && role === 'assistant') {
      const label = document.createElement('span');
      label.className = 'chat-author';
      label.textContent = author;
      node.append(label);
    }
    const content = document.createElement('div');
    content.textContent = body;
    node.append(content);
    $('#chat-messages').append(node);
    $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
    return node;
  }

  function syncComposer() {
    const setupIncomplete = Boolean(shop && !shop.assistant_ready);
    const disabled = busy || awaitingApproval || setupIncomplete;
    $('#chat-input').disabled = disabled;
    $('#chat-send').disabled = disabled;
    $('#chat-send').classList.toggle('is-busy', busy);
    $('#chat-send').setAttribute('aria-label', busy ? 'Đang gửi câu hỏi' : 'Gửi câu hỏi');
    $('#chat-send').setAttribute('aria-busy', busy ? 'true' : 'false');
    $('#chat-input').placeholder = setupIncomplete
      ? 'Trợ lý đang được thiết lập…'
      : awaitingApproval ? 'Đang chờ nhân viên duyệt phản hồi…' : 'Hỏi về dịch vụ…';
  }

  function resizeChatInput() {
    const input = $('#chat-input');
    const maxHeight = 112;
    const previousMaxHeight = input.style.maxHeight;
    input.style.maxHeight = 'none';
    input.style.height = 'auto';
    const naturalHeight = input.scrollHeight;
    input.style.maxHeight = previousMaxHeight;
    input.style.height = `${Math.min(naturalHeight, maxHeight)}px`;
    input.classList.toggle('is-overflowing', naturalHeight > maxHeight);
  }

  function welcome() {
    const box = $('#chat-messages');
    box.replaceChildren();
    const greeting = shop.welcome_message || `Xin chào! Tôi có thể cung cấp thông tin dịch vụ đã được cơ sở cập nhật. Nếu cần đặt lịch, vui lòng liên hệ trực tiếp với cơ sở. Tôi không chẩn đoán hay tư vấn điều trị qua chat.`;
    const currentGreeting = greeting.replace(/(?:Đặt lịch bằng nút bên trên|Bạn có thể đăng ký tư vấn hoặc đặt lịch qua liên kết trong khung chat)/i, 'Bạn có thể hỏi thêm thông tin dịch vụ');
    addBubble('assistant', currentGreeting);
  }

  function renderHistory(messages) {
    const box = $('#chat-messages');
    box.replaceChildren();
    if (!messages.length) {
      welcome();
      return;
    }
    messages.forEach((item) => {
      if (item.role === 'user' || item.role === 'assistant') addBubble(item.role, item.body, item.author || '');
      if (item.role === 'user' && item.status === 'pending') addBubble('notice', 'Câu trả lời đang chờ nhân viên duyệt.');
      if (item.role === 'user' && item.status === 'rejected') addBubble('notice', 'Nhân viên chưa duyệt bản nháp trả lời.');
      if (item.role === 'user' && item.status === 'unavailable') addBubble('notice', 'Tin nhắn đã được lưu nhưng chưa tạo được bản nháp.');
    });
  }

  function renderProducts() {
    const strip = $('#product-strip');
    strip.replaceChildren();
    (shop.products || []).forEach((product) => {
      const card = document.createElement('div');
      card.className = 'product-chip';
      const name = document.createElement('strong');
      name.textContent = product.name;
      const price = document.createElement('span');
      price.textContent = `Giá tham khảo · ${Number(product.price).toLocaleString('vi-VN')} ${product.currency}`;
      card.append(name, price);
      strip.append(card);
    });
    strip.hidden = !shop.products?.length;
  }

  async function startSession() {
    const result = await api(`/public/shops/${encodeURIComponent(slug)}/sessions`, { method: 'POST' });
    token = result.session_token;
    sessionStorage.setItem(storageKey, token);
  }

  async function loadHistory() {
    const result = await api('/public/sessions/me');
    const messages = result.messages || [];
    renderHistory(messages);
    awaitingApproval = messages.some((item) => item.role === 'user' && item.status === 'pending');
    syncComposer();
    if (historyPollTimer !== null) clearTimeout(historyPollTimer);
    historyPollTimer = setTimeout(async () => {
      historyPollTimer = null;
      try { await loadHistory(); } catch (error) { showError(error.message); }
    }, awaitingApproval ? 4000 : 12000);
  }

  function showConsultationSubmitted(submitted) {
    $('#consultation-form').hidden = submitted;
    $('#lead-success').hidden = !submitted;
  }

  async function loadConsultationStatus() {
    const result = await api('/public/sessions/consultation');
    showConsultationSubmitted(Boolean(result.submitted));
  }

  async function connect() {
    if (!slug || (embedded && !parentOrigin)) throw new Error('Không xác định được cơ sở hoặc website nhúng.');
    shop = await api(`/public/shops/${encodeURIComponent(slug)}`);
    document.documentElement.style.setProperty('--shop-color', shop.theme_color || '#159fd1');
    const profileName = shop.name || 'Cơ sở y tế';
    const displayName = profileName.replace(/\s*[·•]\s*bản thử nghiệm\s*$/i, '').trim();
    $('#shop-name').textContent = displayName || profileName;
    $('#shop-subtitle').textContent = 'Thông tin dịch vụ · Hỗ trợ tư vấn';
    $('#mode-note').textContent = shop.mode === 'mock' ? 'Bản thử nghiệm · ' : '';
    $('#chat-status-label').textContent = shop.assistant_ready ? 'Trực tuyến' : 'Đang thiết lập';
    $('#chat-status-badge').classList.toggle('chat-not-ready', !shop.assistant_ready);
    if (!shop.assistant_ready) {
      showError(shop.setup_message || 'Trợ lý đang được quản trị viên thiết lập. Vui lòng quay lại sau.');
      $('#chat-error').classList.add('chat-setup-note');
    } else {
      showError('');
      $('#chat-error').classList.remove('chat-setup-note');
    }
    renderProducts();

    if (!token) await startSession();
    try {
      await loadHistory();
    } catch (error) {
      if (error.status !== 401) throw error;
      sessionStorage.removeItem(storageKey);
      token = '';
      await startSession();
      await loadHistory();
    }
    try { await loadConsultationStatus(); } catch (error) { $('#lead-error').textContent = error.message; }
  }

  $('#chat-input').addEventListener('input', resizeChatInput);
  resizeChatInput();
  $('#lead-close').addEventListener('click', () => $('#consultation-dialog').close());
  $('#consultation-dialog').addEventListener('click', (event) => {
    if (event.target === $('#consultation-dialog')) $('#consultation-dialog').close();
  });
  $('#consultation-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (consultationBusy) return;
    const submitButton = $('#lead-submit');
    consultationBusy = true;
    submitButton.disabled = true;
    submitButton.textContent = 'Đang gửi…';
    $('#lead-error').textContent = '';
    const payload = {
      name: $('#lead-name').value.trim(),
      phone: $('#lead-phone').value.trim(),
      desired_time: $('#lead-time').value.trim(),
      note: $('#lead-note').value.trim(),
      consent: $('#lead-consent').checked,
    };
    try {
      await api('/public/sessions/consultation', { method: 'POST', body: JSON.stringify(payload) });
      showConsultationSubmitted(true);
    } catch (error) {
      $('#lead-error').textContent = error.message;
    } finally {
      consultationBusy = false;
      submitButton.disabled = false;
      submitButton.textContent = 'Gửi yêu cầu đặt lịch';
    }
  });

  $('#chat-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('#chat-input');
    const text = input.value.trim();
    if (!text || busy) return;

    busy = true;
    syncComposer();
    showError('');
    if (pendingText !== text) {
      pendingText = text;
      pendingRequestId = crypto.randomUUID();
    }
    try {
      await api('/public/sessions/messages', {
        method: 'POST',
        body: JSON.stringify({ request_id: pendingRequestId, message: text }),
      });
      input.value = '';
      resizeChatInput();
      pendingText = '';
      pendingRequestId = '';
      await loadHistory();
    } catch (error) {
      showError(error.message);
    } finally {
      busy = false;
      syncComposer();
      input.focus();
    }
  });

  $('#chat-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      $('#chat-form').requestSubmit();
    }
  });

  connect().catch((error) => {
    $('#chat-messages').replaceChildren();
    showError(error.message);
    $('#shop-name').textContent = 'Chưa thể mở chatbot';
    $('#chat-send').disabled = true;
  });
})();

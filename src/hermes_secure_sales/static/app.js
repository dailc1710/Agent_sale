(() => {
  const tokenInput = document.querySelector('#access-token');
  const customerInput = document.querySelector('#customer-id');
  const connectForm = document.querySelector('#connect-form');
  const messageForm = document.querySelector('#message-form');
  const messageInput = document.querySelector('#message-input');
  const sendButton = document.querySelector('.send-button');
  const transcript = document.querySelector('#transcript');
  const errorBanner = document.querySelector('#error-banner');
  const connectionIndicator = document.querySelector('#connection-indicator');
  const sessionLabel = document.querySelector('#session-label');
  const newConversationButton = document.querySelector('#new-conversation');
  const charCount = document.querySelector('#char-count');

  let token = '';
  let role = '';
  let conversationId = sessionStorage.getItem('hermes.conversationId') || '';
  let busy = false;
  let hasPendingDraft = false;

  if (!customerInput.value) {
    customerInput.value = sessionStorage.getItem('hermes.customerId') || crypto.randomUUID();
  }
  syncSessionLabel();

  function syncSessionLabel() {
    sessionLabel.textContent = conversationId
      ? `Cuộc trò chuyện · ${conversationId}`
      : 'Chưa có cuộc trò chuyện';
    newConversationButton.disabled = !token || busy;
  }

  function showError(message) {
    errorBanner.classList.remove('success-message');
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  function clearError() {
    errorBanner.classList.remove('success-message');
    errorBanner.textContent = '';
    errorBanner.hidden = true;
  }

  function tokenRole(value) {
    try {
      const segment = value.split('.')[1];
      const normalized = segment.replace(/-/g, '+').replace(/_/g, '/');
      const decoded = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '='));
      return JSON.parse(decoded).role || '';
    } catch {
      return '';
    }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      let detail = `Yêu cầu thất bại (${response.status}).`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') detail = body.detail;
      } catch { /* Keep the generic status message. */ }
      throw new Error(detail);
    }
    return response.json();
  }

  function statusLabel(status) {
    return ({ pending: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Đã từ chối' })[status] || status;
  }

  function addTextTurn(kind, body, label) {
    const wrapper = document.createElement('article');
    wrapper.className = `turn ${kind}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = body;
    const meta = document.createElement('div');
    meta.className = 'turn-meta';
    meta.textContent = label;
    wrapper.append(bubble, meta);
    transcript.append(wrapper);
  }

  function addDraft(turn) {
    const card = document.createElement('article');
    card.className = 'draft-card';
    const top = document.createElement('div');
    top.className = 'draft-topline';
    const heading = document.createElement('span');
    heading.className = 'draft-label';
    heading.textContent = 'Bản nháp Hermes';
    const badge = document.createElement('span');
    badge.className = `draft-status ${turn.draft_status || ''}`;
    badge.textContent = statusLabel(turn.draft_status);
    top.append(heading, badge);
    const body = document.createElement('p');
    body.className = 'draft-body';
    body.textContent = turn.body;
    card.append(top, body);

    if (turn.draft_status === 'pending' && ['tenant_admin', 'reviewer'].includes(role)) {
      const controls = document.createElement('div');
      controls.className = 'draft-controls';
      const approve = document.createElement('button');
      approve.type = 'button';
      approve.className = 'approve-button';
      approve.textContent = 'Duyệt bản nháp';
      approve.addEventListener('click', () => decideDraft(turn.draft_id, 'approve', approve, reject));
      const reject = document.createElement('button');
      reject.type = 'button';
      reject.className = 'reject-button';
      reject.textContent = 'Từ chối';
      reject.addEventListener('click', () => decideDraft(turn.draft_id, 'reject', reject, approve));
      controls.append(approve, reject);
      card.append(controls);
    } else if (turn.draft_status === 'pending') {
      const note = document.createElement('p');
      note.className = 'decision-note';
      note.textContent = 'Cần reviewer hoặc quản trị viên duyệt trước khi tiếp tục.';
      card.append(note);
    }
    transcript.append(card);
  }

  function renderHistory(turns) {
    transcript.replaceChildren();
    hasPendingDraft = turns.some((turn) => turn.role === 'assistant' && turn.draft_status === 'pending');
    syncComposer();
    if (!turns.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      const title = document.createElement('h3');
      title.textContent = 'Chưa có tin nhắn';
      const text = document.createElement('p');
      text.textContent = 'Hãy nhập một câu hỏi thử nghiệm với dữ liệu tổng hợp.';
      empty.append(title, text);
      transcript.append(empty);
      return;
    }
    for (const turn of turns) {
      if (turn.role === 'customer') addTextTurn('customer', turn.body, 'Khách hàng giả · đã che PII');
      else addDraft(turn);
    }
    transcript.scrollTop = transcript.scrollHeight;
  }

  function syncComposer() {
    const canSend = ['tenant_admin', 'sales_agent'].includes(role) && !hasPendingDraft && !busy;
    messageInput.disabled = !canSend;
    sendButton.disabled = !canSend;
  }

  async function loadHistory() {
    if (!conversationId) return;
    const history = await api(`/chat/conversations/${encodeURIComponent(conversationId)}`);
    renderHistory(history.turns);
    syncSessionLabel();
  }

  async function decideDraft(draftId, decision, clicked, other) {
    clearError();
    clicked.disabled = true;
    other.disabled = true;
    try {
      await api(`/drafts/${encodeURIComponent(draftId)}/${decision}`, { method: 'POST' });
      await loadHistory();
      syncComposer();
      if (!messageInput.disabled) messageInput.focus();
    } catch (error) {
      showError(error.message);
      clicked.disabled = false;
      other.disabled = false;
    }
  }

  connectForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    clearError();
    token = tokenInput.value.trim();
    role = tokenRole(token);
    if (!token) return;
    customerInput.value = customerInput.value.trim();
    sessionStorage.setItem('hermes.customerId', customerInput.value);
    connectionIndicator.classList.add('connected');
    connectionIndicator.setAttribute('aria-label', 'Đã kết nối token trong tab');
    syncComposer();
    newConversationButton.disabled = false;
    try {
      await loadHistory();
    } catch (error) {
      conversationId = '';
      sessionStorage.removeItem('hermes.conversationId');
      syncSessionLabel();
      showError(`${error.message} Kiểm tra token, quyền và ID khách hàng giả.`);
    }
    if (!['tenant_admin', 'sales_agent'].includes(role)) {
      showError('Token này chỉ xem hoặc duyệt bản nháp. Cần sales_agent hoặc tenant_admin để tạo câu hỏi.');
    }
  });

  document.querySelector('#new-customer').addEventListener('click', () => {
    customerInput.value = crypto.randomUUID();
    sessionStorage.setItem('hermes.customerId', customerInput.value);
    conversationId = '';
    sessionStorage.removeItem('hermes.conversationId');
    transcript.replaceChildren();
    syncSessionLabel();
    clearError();
  });

  newConversationButton.addEventListener('click', () => {
    conversationId = '';
    sessionStorage.removeItem('hermes.conversationId');
    transcript.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const title = document.createElement('h3');
    title.textContent = 'Phiên chat mới';
    const text = document.createElement('p');
    text.textContent = 'Sử dụng cùng ID khách hàng giả. Câu trả lời mới vẫn cần được duyệt.';
    empty.append(title, text);
    transcript.append(empty);
    syncSessionLabel();
    clearError();
    messageInput.focus();
  });

  messageForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = messageInput.value.trim();
    if (!token || !message || busy) return;
    busy = true;
    messageInput.disabled = true;
    sendButton.disabled = true;
    newConversationButton.disabled = true;
    clearError();
    sendButton.textContent = '…';
    try {
      const result = await api('/chat/messages', {
        method: 'POST',
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          customer_id: customerInput.value.trim(),
          ...(conversationId ? { conversation_id: conversationId } : {}),
          message,
        }),
      });
      conversationId = result.conversation_id;
      sessionStorage.setItem('hermes.conversationId', conversationId);
      messageInput.value = '';
      charCount.textContent = '0 / 12000';
      await loadHistory();
      showError('Đã tạo bản nháp. Chờ duyệt rồi mới tiếp tục cuộc trò chuyện.');
      errorBanner.classList.add('success-message');
    } catch (error) {
      showError(error.message);
    } finally {
      busy = false;
      sendButton.textContent = '↑';
      syncComposer();
      syncSessionLabel();
    }
  });

  messageInput.addEventListener('input', () => {
    charCount.textContent = `${messageInput.value.length} / 12000`;
  });
})();

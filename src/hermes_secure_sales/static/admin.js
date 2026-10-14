(() => {
  const $ = (selector) => document.querySelector(selector);
  const loginView = $('#login-view');
  const dashboard = $('#dashboard-view');
  const shopSettingsView = $('#shop-settings-view');
  const emptyState = $('#empty-state');
  const shopEditor = $('#shop-editor');
  let shops = [];
  let selected = null;
  let conversations = [];
  let selectedConversationId = null;
  let conversationFilter = 'all';
  let detailsConversationId = null;
  let detailsOpen = false;
  let currentRole = null;

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Không thể hoàn tất yêu cầu.');
    return data;
  }
  function message(target, text, ok = false) { target.textContent = text; target.classList.toggle('ok', ok); }
  function safeText(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; node.textContent = value; return node; }
  function applyRoleVisibility() {
    const isAdmin = currentRole === 'admin';
    $('#open-shop-settings').hidden = !isAdmin;
    $('#new-shop').hidden = !isAdmin;
    $('#empty-new-shop').hidden = !isAdmin;
    if (!isAdmin) {
      shopSettingsView.hidden = true;
      $('#shop-name').value = '';
      $('#shop-slug').value = '';
      $('#welcome-message').value = '';
      $('#theme-color').value = '#159fd1';
      $('#allowed-origins').value = '';
      $('#chat-link').textContent = '';
      $('#embed-code').textContent = '';
      $('#product-list').replaceChildren();
    }
  }
  function setRailActive(activeId) {
    document.querySelectorAll('.messenger-rail .rail-link').forEach((button) => {
      button.classList.toggle('active', button.id === activeId);
    });
  }
  function formatTimestamp(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('vi-VN', { dateStyle: 'medium', timeStyle: 'short' });
  }
  function applyCustomerDetailsVisibility() {
    const wideLayout = window.matchMedia('(min-width: 1100px)').matches;
    const open = Boolean(detailsOpen && detailsConversationId === selectedConversationId);
    $('#customer-details').hidden = !open;
    dashboard.classList.toggle('with-customer-details', open && wideLayout);
    dashboard.classList.toggle('details-open', open && !wideLayout);
    $('#toggle-customer-details').setAttribute('aria-pressed', String(open));
  }
  function toggleCustomerDetails(force) {
    if (!selectedConversationId) return;
    detailsOpen = typeof force === 'boolean' ? force : !detailsOpen;
    applyCustomerDetailsVisibility();
  }
  function embedCode(shop) {
    const src = `${location.origin}/embed/${shop.slug}`;
    return `<iframe src="${src}" title="Chat với ${shop.name.replace(/[<>"&]/g, '')}" loading="lazy" referrerpolicy="strict-origin" style="width:100%;height:680px;border:0;border-radius:16px" allow="clipboard-write"></iframe>`;
  }
  function showLogin() { currentRole = null; applyRoleVisibility(); loginView.hidden = false; dashboard.hidden = true; shopSettingsView.hidden = true; $('#consultations-view').hidden = true; dashboard.classList.remove('thread-open', 'details-open', 'with-customer-details', 'show-consultations'); }
  function showDashboard() { applyRoleVisibility(); loginView.hidden = true; shopSettingsView.hidden = true; dashboard.hidden = false; $('#consultations-view').hidden = true; dashboard.classList.remove('show-consultations'); setRailActive('open-inbox'); }
  function showConsultations() {
    loginView.hidden = true; shopSettingsView.hidden = true; dashboard.hidden = false;
    dashboard.classList.remove('thread-open', 'details-open', 'with-customer-details');
    dashboard.classList.add('show-consultations');
    $('#consultations-view').hidden = false;
    $('#customer-details').hidden = true;
    setRailActive('open-consultations');
    loadConsultations();
  }
  function showShopSettings() {
    if (currentRole !== 'admin') { showDashboard(); message($('#inbox-message'), 'Chỉ quản trị viên mới được vào cài đặt cơ sở.'); return; }
    loginView.hidden = true; dashboard.hidden = true; shopSettingsView.hidden = false;
    loadModelStatus();
    if (selected) loadShopKnowledge(selected.id);
  }
  function renderShopList() {
    const list = $('#shop-list'); list.replaceChildren();
    const shopSelect = $('#consultations-shop-select');
    shopSelect.replaceChildren();
    if (!shops.length) shopSelect.append(new Option('Chưa có cơ sở', ''));
    shops.forEach((shop) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = `shop-choice${selected?.id === shop.id ? ' active' : ''}`;
      const dot = document.createElement('span'); dot.className = 'choice-dot'; dot.style.background = shop.enabled ? '#45ae84' : '#bdc5c0';
      const text = document.createElement('span'); text.append(safeText('span', '', shop.name), safeText('small', '', `/${shop.slug}`));
      button.append(dot, text); button.addEventListener('click', () => selectShop(shop.id)); list.append(button);
      shopSelect.append(new Option(shop.name, shop.id, false, selected?.id === shop.id));
    });
  }
  function renderShop() {
    if (!selected) {
      emptyState.hidden = false; shopEditor.hidden = true; conversations = []; selectedConversationId = null;
      $('#active-shop-label').textContent = 'Chọn cơ sở để bắt đầu';
      $('#active-shop-status').textContent = 'Chưa có cơ sở được chọn';
      $('#consultations-shop-label').textContent = 'Chưa có cơ sở để xem yêu cầu.';
      $('#consultations-empty').textContent = 'Chưa có cơ sở để xem yêu cầu.';
      $('#consultations-table-body').replaceChildren();
      $('#consultations-empty').hidden = false;
      $('.consultations-table-wrap').hidden = true;
      $('#public-chat-link').hidden = true; $('#empty-public-chat-link').hidden = true;
      renderShopList(); renderInboxList(); renderInboxThread(null); return;
    }
    emptyState.hidden = true; shopEditor.hidden = false; renderShopList();
    $('#consultations-shop-label').textContent = `Đang xem yêu cầu gửi tới ${selected.name}.`;
    $('.consultations-table-wrap').hidden = false;
    $('#active-shop-label').textContent = selected.name;
    $('#active-shop-status').textContent = selected.enabled ? 'Trợ lý thông tin đang hoạt động' : 'Trợ lý đang tắt';
    $('#public-chat-link').href = selected.chat_url; $('#public-chat-link').hidden = false;
    $('#empty-public-chat-link').href = selected.chat_url; $('#empty-public-chat-link').hidden = false;
    $('#customer-chat-open').href = selected.chat_url;
    if (currentRole === 'admin') {
      $('#shop-title').textContent = selected.name;
      $('#shop-name').value = selected.name;
      $('#shop-slug').value = selected.slug;
      $('#welcome-message').value = (selected.welcome_message || '').replace(/(?:Đặt lịch bằng nút bên trên|Bạn có thể đăng ký tư vấn hoặc đặt lịch qua liên kết trong khung chat)/i, 'Bạn có thể hỏi thêm thông tin dịch vụ');
      $('#theme-color').value = selected.theme_color;
      $('#allowed-origins').value = selected.allowed_origins.join('\n');
      $('#shop-state').textContent = selected.enabled ? 'Đang bật' : 'Đang tắt';
      $('#toggle-shop').textContent = selected.enabled ? 'Tắt trợ lý' : 'Bật trợ lý';
      $('#open-chat').href = selected.chat_url;
      $('#chat-link').textContent = selected.chat_url;
      $('#embed-code').textContent = embedCode(selected);
      renderProducts();
    }
    loadInbox();
    if (dashboard.classList.contains('show-consultations')) loadConsultations();
  }
  function renderProducts() {
    const list = $('#product-list'); list.replaceChildren();
    if (!selected.products.length) { list.append(safeText('p', 'section-sub', 'Chưa có dịch vụ hoặc gói khám trong danh mục.')); return; }
    selected.products.forEach((product) => {
      const row = document.createElement('div'); row.className = `product-row${product.active ? '' : ' product-muted'}`;
      const title = document.createElement('div'); title.append(safeText('strong', '', product.name), safeText('small', '', `${product.sku}${product.description ? ` · ${product.description}` : ''}`));
      row.append(title, safeText('span', 'price', `${Number(product.price).toLocaleString('vi-VN')} ${product.currency}`));
      const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'button-outline'; edit.textContent = 'Sửa';
      edit.addEventListener('click', () => {
        $('#product-form').dataset.editId = product.id;
        $('#product-name').value = product.name;
        $('#product-sku').value = product.sku;
        $('#product-price').value = product.price;
        $('#product-currency').value = product.currency;
        $('#product-description').value = product.description;
        $('#product-form button[type="submit"]').textContent = 'Lưu dịch vụ';
        $('#product-name').focus();
      });
      const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'button-outline'; toggle.textContent = product.active ? 'Đang bật' : 'Đã tắt';
      toggle.addEventListener('click', async () => { try { await api(`/admin/api/shops/${selected.id}/products/${product.id}`, { method: 'PATCH', body: JSON.stringify({ active: !product.active }) }); await loadShops(selected.id); } catch (e) { message($('#product-message'), e.message); } });
      row.append(edit, toggle); list.append(row);
    });
  }
  function selectShop(id) { selected = shops.find((shop) => shop.id === id) || null; selectedConversationId = null; message($('#shop-message'), ''); message($('#product-message'), ''); message($('#inbox-message'), ''); renderShop(); }
  function setSetupBadge(node, text, ready) {
    node.textContent = text;
    node.classList.toggle('setup-incomplete', !ready);
  }
  async function loadModelStatus() {
    try {
      const status = await api('/admin/api/model/status');
      setSetupBadge($('#model-api-state'), status.configured ? 'Đã cấu hình' : 'Chưa cấu hình', status.configured);
      $('#model-api-description').textContent = status.configured
        ? 'Máy chủ đã nhận cấu hình model ' + status.model + '. Khóa API không hiển thị trong trình duyệt.'
        : 'Chưa có khóa API trên máy chủ. Khóa API được lưu ở Vercel, không nhập vào trang chat hoặc trình duyệt.';
    } catch (error) {
      setSetupBadge($('#model-api-state'), 'Không kiểm tra được', false);
      $('#model-api-description').textContent = error.message;
    }
  }
  async function loadShopKnowledge(shopId) {
    try {
      const data = await api('/admin/api/shops/' + shopId + '/knowledge');
      setSetupBadge(
        $('#knowledge-state'),
        !data.synced ? 'Chưa đồng bộ' : data.api_verified ? 'Sẵn sàng' : 'Cần kiểm tra API',
        Boolean(data.synced && data.api_verified),
      );
      if (data.source_url) $('#knowledge-url').value = data.source_url;
      $('#knowledge-details').textContent = data.synced
        ? data.page_count + ' trang · cập nhật ' + formatTimestamp(data.synced_at) + ' · ' + Number(data.content_length).toLocaleString('vi-VN') + ' ký tự · ' + (data.api_verified ? 'API đã kiểm tra' : 'cần kiểm tra API')
        : 'Chat chỉ mở sau khi API kết nối và website đã đồng bộ.';
    } catch (error) {
      setSetupBadge($('#knowledge-state'), 'Lỗi tải trạng thái', false);
      $('#knowledge-details').textContent = error.message;
    }
  }
  $('#test-model-api').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    message($('#model-api-message'), 'Đang thử kết nối bằng yêu cầu kiểm tra ngắn…');
    try {
      const result = await api('/admin/api/model/test', { method: 'POST', body: '{}' });
      setSetupBadge($('#model-api-state'), 'API hoạt động', true);
      $('#model-api-description').textContent = 'Đã kết nối model ' + result.model + '. Khóa API vẫn được giữ ở máy chủ.';
      message($('#model-api-message'), 'Kết nối API thành công.', true);
      if (selected) await loadShopKnowledge(selected.id);
    } catch (error) {
      setSetupBadge($('#model-api-state'), 'Chưa kết nối', false);
      message($('#model-api-message'), error.message);
    } finally { button.disabled = false; }
  });
  $('#knowledge-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selected) return;
    const button = $('#sync-knowledge');
    button.disabled = true;
    setSetupBadge($('#knowledge-state'), 'Đang đọc website…', false);
    message($('#knowledge-message'), 'Đang đọc nội dung công khai trong cùng tên miền…');
    try {
      const data = await api('/admin/api/shops/' + selected.id + '/knowledge/sync', {
        method: 'POST',
        body: JSON.stringify({ source_url: $('#knowledge-url').value.trim() }),
      });
      setSetupBadge($('#knowledge-state'), 'Đã đồng bộ', true);
      $('#knowledge-url').value = data.source_url;
      $('#knowledge-details').textContent = data.page_count + ' trang · cập nhật ' + formatTimestamp(data.synced_at) + ' · ' + Number(data.content_length).toLocaleString('vi-VN') + ' ký tự · API đã kiểm tra';
      message($('#knowledge-message'), 'Đã kiểm tra API và cập nhật nguồn website. Trợ lý có thể nhận câu hỏi để tạo bản nháp.', true);
    } catch (error) {
      setSetupBadge($('#knowledge-state'), 'Chưa đồng bộ', false);
      message($('#knowledge-message'), error.message);
    } finally { button.disabled = false; }
  });
  async function loadShops(preferredId) {
    shops = await api('/admin/api/shops');
    const next = shops.find((shop) => shop.id === preferredId) || shops.find((shop) => shop.id === selected?.id) || shops[0] || null;
    selected = next; renderShop();
  }
  function renderInboxList() {
    const list = $('#inbox-list'); list.replaceChildren();
    $('#conversation-count').textContent = String(conversations.length);
    const query = $('#conversation-search').value.trim().toLocaleLowerCase('vi');
    const visible = conversations.filter((conversation) => {
      if (conversationFilter === 'review' && !conversation.has_pending_draft) return false;
      return !query || `${conversation.customer_label} ${conversation.last_message || ''}`.toLocaleLowerCase('vi').includes(query);
    });
    if (!visible.length) {
      const note = conversations.length ? 'Không tìm thấy cuộc trò chuyện phù hợp' : 'Chưa có tin nhắn. Mở trang chat tư vấn để bắt đầu.';
      list.append(safeText('p', 'inbox-list-empty', note)); return;
    }
    visible.forEach((conversation) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `inbox-choice${conversation.conversation_id === selectedConversationId ? ' active' : ''}`;
      button.dataset.initial = (conversation.customer_label || 'K').trim().charAt(0).toLocaleUpperCase('vi');
      const top = document.createElement('span'); top.className = 'inbox-choice-top';
      top.append(safeText('strong', '', conversation.customer_label));
      if (conversation.has_pending_draft) top.append(safeText('small', 'inbox-pending-tag', 'Chờ duyệt'));
      const preview = safeText('span', 'inbox-choice-preview', conversation.last_message || 'Chưa có nội dung');
      const time = safeText('small', 'inbox-choice-time', new Date(conversation.last_message_at).toLocaleString('vi-VN'));
      button.append(top, preview, time);
      button.addEventListener('click', () => { selectedConversationId = conversation.conversation_id; dashboard.classList.add('thread-open'); renderInboxList(); loadConversation(); });
      list.append(button);
    });
  }
  function renderInboxThread(conversation) {
    const panel = $('#inbox-thread-panel');
    const empty = $('#inbox-empty');
    const thread = $('#inbox-thread');
    if (!conversation) {
      panel.hidden = true; empty.hidden = false; detailsOpen = false; detailsConversationId = null;
      dashboard.classList.remove('thread-open'); applyCustomerDetailsVisibility(); return;
    }
    panel.hidden = false; empty.hidden = true; thread.replaceChildren();
    if (detailsConversationId !== conversation.conversation_id) {
      detailsConversationId = conversation.conversation_id;
      detailsOpen = window.matchMedia('(min-width: 1100px)').matches;
    }
    $('.customer-avatar').textContent = (conversation.customer_label || 'K').trim().charAt(0).toLocaleUpperCase('vi');
    $('#customer-details-avatar').textContent = (conversation.customer_label || 'K').trim().charAt(0).toLocaleUpperCase('vi');
    $('#inbox-thread-title').replaceChildren(
      safeText('strong', '', conversation.customer_label),
      safeText('small', '', conversation.active ? 'Phiên chat đang hoạt động' : 'Phiên chat đã hết hạn')
    );
    $('#detail-customer-name').textContent = conversation.customer_label || 'Khách ẩn danh';
    $('#detail-customer-reference').textContent = `Mã ẩn danh · ${(conversation.customer_label || '').replace(/^Khách\s*/i, '') || '—'}`;
    $('#detail-session-status').textContent = conversation.active ? 'Đang hoạt động' : 'Đã hết hạn';
    $('#detail-chat-code').textContent = `HD-${String(conversation.conversation_id).slice(-6).toUpperCase()}`;
    $('#detail-started-at').textContent = formatTimestamp(conversation.started_at);
    $('#detail-last-activity').textContent = formatTimestamp(conversation.messages.at(-1)?.created_at);
    $('#detail-message-count').textContent = String(conversation.messages.length);
    const pendingDrafts = conversation.messages.filter((item) => item.draft?.status === 'pending').length;
    $('#detail-pending-drafts').textContent = pendingDrafts ? `${pendingDrafts} bản nháp` : 'Không có';
    applyCustomerDetailsVisibility();
    conversation.messages.forEach((item) => {
      const card = document.createElement('article');
      card.className = `inbox-message ${item.role === 'user' ? 'customer' : 'reply'}`;
      const head = document.createElement('div'); head.className = 'inbox-message-head';
      head.append(safeText('strong', '', item.author || (item.role === 'user' ? 'Khách' : 'Hermes')));
      head.append(safeText('time', '', new Date(item.created_at).toLocaleString('vi-VN')));
      card.append(head, safeText('div', 'inbox-message-body', item.body));
      if (item.role === 'user' && item.draft && item.draft.status !== 'approved') {
        const draft = document.createElement('div'); draft.className = `inbox-draft ${item.draft.status}`;
        const state = item.draft.status === 'pending' ? 'Bản nháp Hermes · chờ duyệt' : 'Bản nháp Hermes · đã từ chối';
        draft.append(safeText('strong', '', state), safeText('div', 'inbox-message-body', item.draft.body));
        if (item.draft.decision_note) draft.append(safeText('small', '', item.draft.decision_note));
        if (item.draft.status === 'pending' && currentRole === 'admin') {
          const actions = document.createElement('div'); actions.className = 'inbox-draft-actions';
          const approve = document.createElement('button'); approve.type = 'button'; approve.className = 'button-green'; approve.textContent = 'Duyệt và hiện cho khách';
          const reject = document.createElement('button'); reject.type = 'button'; reject.className = 'button-outline button-danger'; reject.textContent = 'Từ chối';
          approve.addEventListener('click', () => decideDraft(item.draft.id, 'approve'));
          reject.addEventListener('click', () => decideDraft(item.draft.id, 'reject'));
          actions.append(approve, reject); draft.append(actions);
        }
        card.append(draft);
      } else if (item.role === 'user' && item.status === 'unavailable') {
        card.append(safeText('small', 'inbox-draft-note', 'Chưa có bản nháp trả lời cho tin này.'));
      }
      thread.append(card);
    });
    thread.scrollTop = thread.scrollHeight;
    const canReply = conversation.active && conversation.messages.some((item) => item.role === 'user');
    $('#staff-reply-form').hidden = !canReply;
    $('#staff-reply').disabled = !canReply;
    $('#staff-reply-form button[type="submit"]').disabled = !canReply;
  }
  async function loadConversation() {
    if (!selected || !selectedConversationId) { renderInboxThread(null); return; }
    const shopId = selected.id;
    const conversationId = selectedConversationId;
    try {
      const conversation = await api(`/admin/api/shops/${shopId}/conversations/${conversationId}`);
      if (selected?.id !== shopId || selectedConversationId !== conversationId) return;
      renderInboxThread(conversation);
    } catch (e) { message($('#inbox-message'), e.message); }
  }
  async function loadInbox() {
    if (!selected) return;
    const shopId = selected.id;
    try {
      const rows = await api(`/admin/api/shops/${shopId}/conversations`);
      if (selected?.id !== shopId) return;
      conversations = rows;
      if (!conversations.some((item) => item.conversation_id === selectedConversationId)) selectedConversationId = conversations[0]?.conversation_id || null;
      renderInboxList();
      if (selectedConversationId) await loadConversation(); else renderInboxThread(null);
    } catch (e) { message($('#inbox-message'), e.message); }
  }
  async function loadConsultations() {
    const body = $('#consultations-table-body');
    if (!selected) {
      body.replaceChildren();
      $('.consultations-table-wrap').hidden = true;
      $('#consultations-empty').hidden = false;
      $('#consultations-empty').textContent = 'Chưa có cơ sở để xem yêu cầu.';
      return;
    }
    const shopId = selected.id;
    body.replaceChildren();
    $('.consultations-table-wrap').hidden = false;
    try {
      const rows = await api(`/admin/api/shops/${shopId}/consultation-requests`);
      if (selected?.id !== shopId) return;
      body.replaceChildren();
      $('#consultations-empty').hidden = true;
      if (!rows.length) {
        const emptyRow = document.createElement('tr');
        const emptyCell = safeText('td', 'consultations-empty-cell', 'Chưa có khách gửi biểu mẫu tư vấn.');
        emptyCell.colSpan = 6;
        emptyRow.append(emptyCell);
        body.append(emptyRow);
        return;
      }
      rows.forEach((row) => {
        const tr = document.createElement('tr');
        tr.append(
          safeText('td', '', row.source === 'google_forms' ? 'Google Biểu mẫu' : 'Chatbot'),
          safeText('td', '', formatTimestamp(row.created_at)),
          safeText('td', '', row.name),
          safeText('td', '', row.phone),
          safeText('td', '', row.desired_time),
          safeText('td', '', row.note || '—'),
        );
        body.append(tr);
      });
      message($('#consultations-message'), '', true);
    } catch (e) { message($('#consultations-message'), e.message); }
  }
  async function decideDraft(draftId, decision) {
    if (!selected) return;
    try {
      await api(`/admin/api/shops/${selected.id}/drafts/${draftId}/${decision}`, { method: 'POST', body: '{}' });
      message($('#inbox-message'), decision === 'approve' ? 'Đã duyệt; bản trả lời Hermes sẽ hiện trong chat.' : 'Đã từ chối bản nháp Hermes.', true);
      await loadInbox();
    } catch (e) { message($('#inbox-message'), e.message); }
  }
  $('#inbox-refresh').addEventListener('click', loadInbox);
  $('#conversation-search').addEventListener('input', renderInboxList);
  document.querySelectorAll('[data-inbox-filter]').forEach((button) => button.addEventListener('click', () => {
    conversationFilter = button.dataset.inboxFilter;
    document.querySelectorAll('[data-inbox-filter]').forEach((item) => item.classList.toggle('active', item === button));
    renderInboxList();
  }));
  $('#open-shop-settings').addEventListener('click', showShopSettings);
  $('#open-inbox').addEventListener('click', showDashboard);
  $('#open-consultations').addEventListener('click', showConsultations);
  $('#consultations-refresh').addEventListener('click', loadConsultations);
  $('#consultations-shop-select').addEventListener('change', (event) => selectShop(event.target.value));
  $('#settings-back').addEventListener('click', showDashboard);
  $('#toggle-customer-details').addEventListener('click', () => toggleCustomerDetails());
  $('#close-customer-details').addEventListener('click', () => toggleCustomerDetails(false));
  $('#thread-back').addEventListener('click', () => { dashboard.classList.remove('thread-open'); toggleCustomerDetails(false); });
  window.addEventListener('resize', applyCustomerDetailsVisibility);
  const staffReplyInput = $('#staff-reply');
  function resizeStaffReplyInput() {
    staffReplyInput.style.height = 'auto';
    const maxHeight = Number.parseFloat(getComputedStyle(staffReplyInput).maxHeight) || 130;
    const contentHeight = staffReplyInput.scrollHeight;
    staffReplyInput.style.height = `${Math.min(contentHeight, maxHeight)}px`;
    staffReplyInput.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden';
  }
  staffReplyInput.addEventListener('input', resizeStaffReplyInput);
  resizeStaffReplyInput();
  $('#staff-reply-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selected || !selectedConversationId) return;
    const input = $('#staff-reply');
    const button = $('#staff-reply-form button[type="submit"]');
    const body = input.value.trim();
    if (!body) return;
    input.disabled = true; button.disabled = true;
    try {
      await api(`/admin/api/shops/${selected.id}/conversations/${selectedConversationId}/messages`, {
        method: 'POST', body: JSON.stringify({ message: body }),
      });
      input.value = '';
      resizeStaffReplyInput();
      message($('#inbox-message'), 'Đã gửi tin nhắn của nhân viên cho khách.', true);
      await loadInbox();
    } catch (e) { message($('#inbox-message'), e.message); }
    finally { input.disabled = false; button.disabled = false; }
  });
  setInterval(() => { if (!dashboard.hidden && selected) { if (dashboard.classList.contains('show-consultations')) loadConsultations(); else loadInbox(); } }, 15000);
  async function checkLogin() {
    try { const user = await api('/admin/api/me'); currentRole = user.role; $('#sidebar-role-label').textContent = currentRole === 'admin' ? 'Hộp thư y tế · Quản trị viên' : 'Hộp thư y tế · Nhân viên'; showDashboard(); await loadShops(); }
    catch { showLogin(); }
  }
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); message($('#login-message'), '');
    try { const user = await api('/admin/api/login', { method: 'POST', body: JSON.stringify({ username: $('#username').value, password: $('#password').value }) }); currentRole = user.role; $('#sidebar-role-label').textContent = currentRole === 'admin' ? 'Hộp thư y tế · Quản trị viên' : 'Hộp thư y tế · Nhân viên'; $('#password').value = ''; showDashboard(); await loadShops(); }
    catch (e) { message($('#login-message'), e.message); }
  });
  $('#logout').addEventListener('click', async () => { try { await api('/admin/api/logout', { method: 'POST', body: '{}' }); } finally { showLogin(); } });
  function openCreateShop() {
    const name = prompt('Tên cơ sở y tế:', 'Cơ sở y tế mới'); if (!name) return;
    const slug = prompt('Mã đường dẫn (chữ thường, số, dấu gạch ngang):', name.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));
    if (!slug) return;
    api('/admin/api/shops', { method: 'POST', body: JSON.stringify({ name, slug, welcome_message: 'Xin chào! Tôi có thể cung cấp thông tin dịch vụ đã được cơ sở cập nhật và hướng dẫn gửi yêu cầu đặt lịch. Tôi không chẩn đoán hay tư vấn điều trị qua chat.', theme_color: '#159fd1', allowed_origins: [] }) })
      .then((shop) => loadShops(shop.id)).catch((e) => alert(e.message));
  }
  $('#new-shop').addEventListener('click', openCreateShop);
  $('#empty-new-shop').addEventListener('click', openCreateShop);
  $('#shop-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (!selected) return;
    try {
      await api(`/admin/api/shops/${selected.id}`, { method: 'PUT', body: JSON.stringify({ name: $('#shop-name').value, welcome_message: $('#welcome-message').value, theme_color: $('#theme-color').value, allowed_origins: $('#allowed-origins').value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean), enabled: selected.enabled }) });
      await loadShops(selected.id); message($('#shop-message'), 'Đã lưu cấu hình cơ sở.', true);
    } catch (e) { message($('#shop-message'), e.message); }
  });
  $('#toggle-shop').addEventListener('click', async () => {
    if (!selected) return;
    try { await api(`/admin/api/shops/${selected.id}`, { method: 'PUT', body: JSON.stringify({ name: $('#shop-name').value, welcome_message: $('#welcome-message').value, theme_color: $('#theme-color').value, allowed_origins: $('#allowed-origins').value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean), enabled: !selected.enabled }) }); await loadShops(selected.id); message($('#shop-message'), selected.enabled ? 'Trợ lý đã được bật.' : 'Trợ lý đã được tắt.', true); }
    catch (e) { message($('#shop-message'), e.message); }
  });
  $('#product-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (!selected) return;
    try {
      const editId = event.target.dataset.editId;
      const body = { name: $('#product-name').value, sku: $('#product-sku').value, price: $('#product-price').value, currency: $('#product-currency').value.toUpperCase(), description: $('#product-description').value };
      if (editId) await api(`/admin/api/shops/${selected.id}/products/${editId}`, { method: 'PATCH', body: JSON.stringify(body) });
      else await api(`/admin/api/shops/${selected.id}/products`, { method: 'POST', body: JSON.stringify(body) });
      delete event.target.dataset.editId; event.target.reset(); $('#product-currency').value = 'VND'; $('#product-form button[type="submit"]').textContent = 'Thêm dịch vụ';
      await loadShops(selected.id); message($('#product-message'), editId ? 'Đã lưu dịch vụ.' : 'Đã thêm dịch vụ.', true);
    } catch (e) { message($('#product-message'), e.message); }
  });
  async function copy(text, target) { try { await navigator.clipboard.writeText(text); message(target, 'Đã sao chép.', true); setTimeout(() => message(target, ''), 1800); } catch { message(target, 'Không thể sao chép tự động trên trang này.'); } }
  $('#copy-chat').addEventListener('click', () => selected && copy(selected.chat_url, $('#shop-message')));
  $('#copy-embed').addEventListener('click', () => selected && copy(embedCode(selected), $('#shop-message')));
  checkLogin();
})();

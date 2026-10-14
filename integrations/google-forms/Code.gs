/**
 * Google Forms -> Hermes Secure Sales Hub.
 * Bind this Apps Script project to the Google Form, configure Script Properties,
 * then run installGoogleFormsTrigger() once as a form editor.
 */
const FIELD_TITLES = {
  name: 'Họ và tên',
  phone: 'Số điện thoại',
  desiredTime: 'Thời gian mong muốn',
  note: 'Ghi chú / triệu chứng',
  consent: 'Đồng ý xử lý thông tin',
};
const CONSENT_ANSWER = 'Tôi đồng ý cho cơ sở y tế dùng thông tin này để liên hệ về yêu cầu tư vấn/đặt lịch.';
const HANDLER_NAME = 'onGoogleFormSubmit';

function installGoogleFormsTrigger() {
  const config = readConfig_();
  const form = FormApp.openById(config.formId);
  const exists = ScriptApp.getProjectTriggers().some((trigger) =>
    trigger.getHandlerFunction() === HANDLER_NAME && trigger.getTriggerSourceId() === form.getId()
  );
  if (!exists) {
    ScriptApp.newTrigger(HANDLER_NAME).forForm(form).onFormSubmit().create();
  }
}

function onGoogleFormSubmit(event) {
  if (!event || !event.response) throw new Error('Expected a Google Forms submit event.');
  const config = readConfig_();
  const response = event.response;
  const answers = {};
  response.getItemResponses().forEach((itemResponse) => {
    answers[itemResponse.getItem().getTitle()] = itemResponse.getResponse();
  });

  const consentAnswer = answers[FIELD_TITLES.consent];
  const consentValues = Array.isArray(consentAnswer) ? consentAnswer : [consentAnswer];
  const payload = {
    response_id: response.getId(),
    form_id: event.source.getId(),
    submitted_at: response.getTimestamp().toISOString(),
    name: scalarAnswer_(answers[FIELD_TITLES.name]),
    phone: scalarAnswer_(answers[FIELD_TITLES.phone]),
    desired_time: scalarAnswer_(answers[FIELD_TITLES.desiredTime]),
    note: scalarAnswer_(answers[FIELD_TITLES.note]),
    consent: consentValues.some((value) => String(value || '').trim() === CONSENT_ANSWER),
  };

  if (payload.form_id !== config.formId) throw new Error('Form ID does not match the configured form.');
  const body = JSON.stringify(payload);
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = hmacHex_(timestamp + '.' + body, config.secret);
  const responseFromHub = UrlFetchApp.fetch(
    config.apiBaseUrl + '/integrations/google-forms/' + encodeURIComponent(config.shopSlug),
    {
      method: 'post',
      contentType: 'application/json',
      payload: body,
      headers: {
        'X-Hermes-Timestamp': timestamp,
        'X-Hermes-Signature': 'sha256=' + signature,
      },
      muteHttpExceptions: true,
    }
  );
  const status = responseFromHub.getResponseCode();
  if (status < 200 || status >= 300) {
    // Do not write the response payload or submitted answers to Apps Script logs.
    console.error('Hermes Forms sync failed with HTTP ' + status + '.');
    throw new Error('Hermes Forms sync failed with HTTP ' + status + '.');
  }
}

function readConfig_() {
  const properties = PropertiesService.getScriptProperties();
  const apiBaseUrl = String(properties.getProperty('HERMES_API_BASE_URL') || '').replace(/\/$/, '');
  const shopSlug = String(properties.getProperty('HERMES_SHOP_SLUG') || '');
  const formId = String(properties.getProperty('GOOGLE_FORM_ID') || '');
  const secret = String(properties.getProperty('HERMES_WEBHOOK_SECRET') || '');
  if (!/^https:\/\//i.test(apiBaseUrl) || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(shopSlug) || !formId || secret.length < 32) {
    throw new Error('Set HERMES_API_BASE_URL, HERMES_SHOP_SLUG, GOOGLE_FORM_ID and a 32+ character HERMES_WEBHOOK_SECRET in Script Properties.');
  }
  return { apiBaseUrl: apiBaseUrl, shopSlug: shopSlug, formId: formId, secret: secret };
}

function scalarAnswer_(value) {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ').trim();
  return value === null || value === undefined ? '' : String(value).trim();
}

function hmacHex_(message, secret) {
  return Utilities.computeHmacSha256Signature(message, secret, Utilities.Charset.UTF_8)
    .map((byte) => ('0' + ((byte + 256) % 256).toString(16)).slice(-2))
    .join('');
}


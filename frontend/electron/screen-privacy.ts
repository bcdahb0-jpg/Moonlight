/**
 * 主进程隐私前置规则（截图前快速阻断）。
 *
 * 与后端 backend/.../screen_awareness/privacy.py 的核心规则保持一致：
 * 敏感窗口（密码/登录/支付/凭据）、Moonlight 自身窗口、标题关键词。
 * 后端在收到帧时还会二次校验——这里只是第一道闸，减少无谓截图。
 */

const MOONLIGHT_PATTERNS = ['moonlight', '月光', '小月'];
const MOONLIGHT_APPS = ['moonlight'];

const SENSITIVE_APPS = [
  '1password',
  'bitwarden',
  'keepass',
  'lastpass',
  'kaspersky',
  'norton',
  'windowssecurity',
  'credentialmanager',
  'password',
  'paypal',
  'alipay',
  'wechatpay',
  'unionpay',
  '网银',
  '银行',
];

const SENSITIVE_TITLE_KEYWORDS = [
  'password',
  'passwort',
  'parol',
  'senha',
  '密碼',
  '密码',
  '口令',
  '登录',
  '登陆',
  'sign in',
  'signin',
  'log in',
  'login',
  '支付',
  '付款',
  '结账',
  'checkout',
  '银行卡',
  '验证码',
  'otp',
  '2fa',
  'two-factor',
  'credential',
  'secret',
  '私密浏览',
  'incognito',
];

function norm(s: string | undefined | null): string {
  return (s ?? '').trim().toLowerCase();
}

/** 返回阻断原因；null = 允许截图。 */
export function privacyBlock(title: string, app: string): string | null {
  const t = norm(title);
  const a = norm(app);

  for (const p of MOONLIGHT_PATTERNS) {
    if (t.includes(p)) return 'moonlight_self';
  }
  if (MOONLIGHT_APPS.includes(a)) return 'moonlight_self';

  if (a) {
    for (const s of SENSITIVE_APPS) {
      if (a.includes(s)) return 'sensitive_app';
    }
  }
  for (const kw of SENSITIVE_TITLE_KEYWORDS) {
    if (t.includes(kw)) return 'title_keyword';
  }
  return null;
}

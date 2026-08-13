/**
 * 采集前阻断规则（渲染进程，第一道闸）。
 *
 * 与 Electron 主进程 screen-privacy.ts / 后端 screen_awareness/privacy.py
 * 核心规则一致：Moonlight 自身、敏感应用、标题关键词。用户自定义黑名单在
 * 设置页维护（存 LocalSettings），这里与内置规则合并后做「采集前」判定，
 * 命中即不发起截图请求（省资源；主进程/后端仍会二次兜底）。
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

export interface PrivacyUserRules {
  blockedApps: string[];
  blockedTitleKeywords: string[];
}

function norm(s: string | undefined | null): string {
  return (s ?? '').trim().toLowerCase();
}

/** 返回阻断原因；null = 允许采集。 */
export function checkPrivacyBlock(
  title: string,
  app: string,
  userRules?: Partial<PrivacyUserRules>,
): string | null {
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
  // 用户自定义规则。
  const blockedApps = (userRules?.blockedApps ?? []).map(norm).filter(Boolean);
  if (a && blockedApps.includes(a)) return 'user_blocklist_app';
  const blockedKws = (userRules?.blockedTitleKeywords ?? []).map(norm).filter(Boolean);
  for (const kw of blockedKws) {
    if (t.includes(kw)) return 'user_blocklist_title';
  }
  return null;
}

from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

if 'FEATURE_VISIBILITY_ENFORCEMENT_V1' not in s:
    code = r'''# FEATURE_VISIBILITY_ENFORCEMENT_V1
# Manager feature switches control user-facing visibility and stale callback access.

V25_FEATURE_MENU_LABELS = {
    '🧠 مرکز من':'unified_hub','🧠 My Center':'unified_hub',
    '💰 سرمایه‌های من':'portfolio','💰 My Portfolio':'portfolio',
    '💳 اقساط':'installments','💳 Installments':'installments',
    '👤 اطلاعات من':'profile_sharing','👤 My Profile':'profile_sharing',
    '🎙️ دستیار صوتی':'voice','🎙️ Voice Assistant':'voice',
    '📅 تقویم من':'calendar_hub','📅 My Calendar':'calendar_hub',
    '🏪 پنل کسب‌وکار':'business_services','🏪 Business Panel':'business_services',
}
try:
    FEATURE_MENU_MAP.update(V25_FEATURE_MENU_LABELS)
except Exception:
    pass

_FEATURE_CALLBACK_PREFIXES = {
    'goals:':'goals','price:':'price_data','support:':'support','vip:':'vip','settings:':'settings',
    'v25:today':'unified_hub','v25:reminders':'important_reminders','v25:calendar':'calendar_hub',
    'v25:portfolio':'portfolio','v25:installments':'installments','v25:profile':'profile_sharing',
    'v25:voice':'voice','v25:vip':'vip_plans','v25:business':'business_services','v25:services':'business_services',
    'v25:booklink':'customer_online_booking','v25:bookservice':'customer_online_booking',
    'v25:bookingcard':'booking_payments','v25:survey_rate':'surveys','v25:surveycomment':'surveys',
    'v25:viponline':'vip_plans','v25:vipcard':'vip_plans','v25:inst':'installments','v25:rem':'important_reminders',
    'v25:customers':'customers','v25:msgcustomer':'customers','v25:bizfinance':'business_finance','v25:sms':'sms',
}

def _feature_key_from_callback(data):
    data = str(data or '')
    if data.startswith('v25:master:') or data.startswith('feat:') or data.startswith('featinfo:'):
        return None
    for prefix, key in sorted(_FEATURE_CALLBACK_PREFIXES.items(), key=lambda x: len(x[0]), reverse=True):
        if data.startswith(prefix):
            return key
    return None

def _feature_blocked(uid, key):
    if not key or admin_is_allowed(uid):
        return False
    try:
        if 'user_feature_allowed' in globals():
            return not bool(user_feature_allowed(uid, key))
        if 'v25_allowed' in globals():
            return not bool(v25_allowed(uid, key))
        if 'feature_enabled' in globals():
            return not bool(feature_enabled(key))
    except Exception:
        logger.exception('Feature gate check failed: %s', key)
    return False

def _feature_block_text(uid):
    return ('⛔ این قابلیت فعلاً توسط مدیر غیرفعال شده است.'
            if lang(uid) == 'fa' else
            '⛔ This feature is currently disabled by the administrator.')

def _wrap_feature_callback_handler(fn):
    async def _wrapped(update, context):
        q = getattr(update, 'callback_query', None)
        uid = q.from_user.id if q and q.from_user else update.effective_user.id
        key = _feature_key_from_callback(q.data if q else '')
        if _feature_blocked(uid, key):
            try:
                await q.answer(_feature_block_text(uid), show_alert=True)
            except Exception:
                pass
            return
        return await fn(update, context)
    _wrapped._feature_gate_wrapped = True
    return _wrapped

for _name in (
    'v25_callback','price_callback','support_callback','vip_callback',
    'goals_navigation_callback','settings_callback',
    'compact_section_callback','compact_menu_callback'
):
    _fn = globals().get(_name)
    if _fn is not None and not getattr(_fn, '_feature_gate_wrapped', False):
        globals()[_name] = _wrap_feature_callback_handler(_fn)

if 'keyboard' in globals() and not getattr(keyboard, '_feature_visibility_wrapped', False):
    _old_keyboard = keyboard
    def keyboard(uid):
        kb = _old_keyboard(uid)
        try:
            rows = []
            for row in getattr(kb, 'keyboard', []):
                keep = [
                    label for label in row
                    if FEATURE_MENU_MAP.get(label) is None
                    or not _feature_blocked(uid, FEATURE_MENU_MAP[label])
                ]
                if keep:
                    rows.append(keep)
            return ReplyKeyboardMarkup(rows, resize_keyboard=True)
        except Exception:
            return kb
    keyboard._feature_visibility_wrapped = True
'''
    s += '\n\n' + code
    BOT.write_text(s, encoding='utf-8')
    print('Feature visibility patch applied')
else:
    print('Feature visibility patch already present')

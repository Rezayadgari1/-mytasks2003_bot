from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

if 'FEATURE_CONTROL_FINAL_V2' not in s:
    code = r'''# FEATURE_CONTROL_FINAL_V2
# Final feature-control layer. Disabled features must disappear from every user menu
# and must reject both direct callbacks and text flows. Admins are never blocked.

FEATURE_CONTROL_ALIASES = {
    'weekly': 'weekly',
    'weekly_table': 'weekly',
    'referral': 'referrals',
    'referrals': 'referrals',
    'price': 'price_data',
    'price_data': 'price_data',
    'customer': 'customers',
    'customers': 'customers',
    'ai_chat': 'ai',
}

FEATURE_CONTROL_CATEGORIES = [
    ('🎯 اهداف و برنامه‌ریزی', [
        ('goals', '🎯 اهداف'),
        ('prepared_goals', '📋 اهداف آماده'),
        ('edit', '✏️ ویرایش اهداف'),
        ('weekly', '📅 جدول هفتگی'),
        ('stats', '📊 آمار'),
        ('important_reminders', '🔔 یادآوری‌های مهم'),
        ('calendar_hub', '🗓 تقویم'),
    ]),
    ('👤 حساب کاربری', [
        ('profile', '👤 پروفایل'),
        ('profile_sharing', '👤 اطلاعات من'),
        ('achievements', '🏆 دستاوردها'),
        ('xp', '⭐ XP'),
        ('referrals', '🤝 دعوت دوستان'),
    ]),
    ('🤖 هوش مصنوعی و ابزارها', [
        ('ai', '🤖 چت با هوش مصنوعی'),
        ('voice', '🎙️ دستیار صوتی'),
        ('unified_hub', '🧠 مرکز من'),
    ]),
    ('💰 مالی و قیمت‌ها', [
        ('price_data', '📈 قیمت آنلاین'),
        ('portfolio', '💰 سرمایه‌های من'),
        ('installments', '💳 اقساط'),
    ]),
    ('💎 VIP و خدمات', [
        ('vip', '💎 VIP'),
        ('vip_plans', '💎 پلن‌های VIP'),
        ('support', '🎫 پشتیبانی'),
    ]),
    ('🏪 کسب‌وکار', [
        ('business_services', '🏪 پنل کسب‌وکار'),
        ('customers', '👥 مشتریان'),
        ('customer_online_booking', '📅 رزرو آنلاین'),
        ('booking_payments', '💳 پرداخت رزرو'),
        ('business_finance', '💵 مالی کسب‌وکار'),
        ('surveys', '📝 نظرسنجی‌ها'),
        ('sms', '📱 پیامک'),
    ]),
    ('⚙️ سیستم', [
        ('settings', '⚙️ تنظیمات'),
        ('maintenance', '🛠 حالت تعمیرات'),
    ]),
]

# Add any feature keys already defined elsewhere in the bot, so the switchboard
# cannot silently miss a capability added by an earlier module.
try:
    _known = {k for _, items in FEATURE_CONTROL_CATEGORIES for k, _ in items}
    for _label, _key in FEATURE_MENU_MAP.items():
        if _key and _key not in _known:
            FEATURE_CONTROL_CATEGORIES.append(('🧩 سایر قابلیت‌ها', [(_key, str(_label))]))
            _known.add(_key)
except Exception:
    pass


def _fc_admin(uid):
    try:
        return bool(admin_is_allowed(uid))
    except Exception:
        return int(uid or 0) in ADMIN_IDS


def _fc_key(key):
    return FEATURE_CONTROL_ALIASES.get(str(key or '').strip(), str(key or '').strip())


def _fc_enabled(uid, key):
    key = _fc_key(key)
    if not key or _fc_admin(uid):
        return True
    try:
        if 'feature_access_mode' in globals():
            if str(feature_access_mode(key) or '').lower() == 'off':
                return False
    except Exception:
        pass
    try:
        if 'feature_enabled' in globals() and not bool(feature_enabled(key)):
            return False
    except Exception:
        pass
    try:
        if 'user_feature_allowed' in globals() and not bool(user_feature_allowed(uid, key)):
            return False
    except Exception:
        pass
    return True


def _fc_label_key(label):
    try:
        key = FEATURE_MENU_MAP.get(str(label))
        if key:
            return _fc_key(key)
    except Exception:
        pass
    return None


def _fc_callback_key(data):
    data = str(data or '')
    exact = {
        'aichat:back': 'ai',
        'v25:ai': 'ai',
        'ai:chat': 'ai',
        'ai:back': 'ai',
        'v25:voice': 'voice',
        'v25:portfolio': 'portfolio',
        'v25:installments': 'installments',
        'v25:profile': 'profile_sharing',
        'v25:calendar': 'calendar_hub',
        'v25:customers': 'customers',
        'v25:business': 'business_services',
        'v25:services': 'business_services',
        'v25:sms': 'sms',
        'v25:bizfinance': 'business_finance',
    }
    if data in exact:
        return exact[data]
    prefixes = [
        ('aichat:', 'ai'), ('ai:', 'ai'), ('v25:ai:', 'ai'),
        ('v25:voice:', 'voice'), ('v25:portfolio:', 'portfolio'),
        ('v25:installments:', 'installments'), ('v25:inst:', 'installments'),
        ('v25:profile:', 'profile_sharing'), ('v25:calendar:', 'calendar_hub'),
        ('v25:customers:', 'customers'), ('v25:msgcustomer:', 'customers'),
        ('v25:business:', 'business_services'), ('v25:services:', 'business_services'),
        ('v25:booklink:', 'customer_online_booking'), ('v25:bookservice:', 'customer_online_booking'),
        ('v25:bookingcard:', 'booking_payments'), ('v25:survey_rate:', 'surveys'),
        ('v25:surveycomment:', 'surveys'), ('v25:bizfinance:', 'business_finance'),
        ('v25:sms:', 'sms'), ('v25:vip:', 'vip_plans'), ('v25:viponline:', 'vip_plans'),
        ('v25:vipcard:', 'vip_plans'), ('v25:rem:', 'important_reminders'),
        ('v25:reminders:', 'important_reminders'), ('v25:today:', 'unified_hub'),
        ('goals:', 'goals'), ('price:', 'price_data'), ('support:', 'support'),
        ('vip:', 'vip'), ('settings:', 'settings'),
    ]
    for prefix, key in prefixes:
        if data.startswith(prefix):
            return key
    return None


def _fc_block_message(uid):
    return ('⛔ این قابلیت فعلاً توسط مدیر غیرفعال شده است.' if lang(uid) == 'fa'
            else '⛔ This feature is currently disabled by the administrator.')


def _fc_filter_markup(uid, markup):
    if _fc_admin(uid) or markup is None:
        return markup
    try:
        if isinstance(markup, ReplyKeyboardMarkup):
            out = []
            for row in markup.keyboard:
                keep = []
                for label in row:
                    key = _fc_label_key(label)
                    if not key or _fc_enabled(uid, key):
                        keep.append(label)
                if keep:
                    out.append(keep)
            return ReplyKeyboardMarkup(out, resize_keyboard=True, one_time_keyboard=False)
        if isinstance(markup, InlineKeyboardMarkup):
            out = []
            for row in markup.inline_keyboard:
                keep = []
                for btn in row:
                    key = _fc_callback_key(getattr(btn, 'callback_data', None))
                    if key and not _fc_enabled(uid, key):
                        continue
                    keep.append(btn)
                if keep:
                    out.append(keep)
            return InlineKeyboardMarkup(out)
    except Exception:
        logger.exception('Feature markup filtering failed')
    return markup


# Final wrapper around the final keyboard() definition. This is deliberately
# applied after all previous compatibility layers, which were redefining keyboard.
try:
    _fc_old_keyboard = keyboard
    if not getattr(_fc_old_keyboard, '_fc_final_wrapped', False):
        def keyboard(uid):
            return _fc_filter_markup(uid, _fc_old_keyboard(uid))
        keyboard._fc_final_wrapped = True
except Exception:
    pass


# Filter the Smart Tools inline keyboard, including AI, after every rebuild.
for _fn_name in ('_compact_menu_keyboard', 'compact_menu_keyboard', 'tools_keyboard', 'smart_tools_keyboard'):
    try:
        _old = globals().get(_fn_name)
        if _old and not getattr(_old, '_fc_final_wrapped', False):
            def _make_markup_wrapper(fn):
                def _wrapped(uid, *args, **kwargs):
                    return _fc_filter_markup(uid, fn(uid, *args, **kwargs))
                _wrapped._fc_final_wrapped = True
                return _wrapped
            globals()[_fn_name] = _make_markup_wrapper(_old)
    except Exception:
        pass


# Block direct feature callbacks. This catches callbacks even when a user still
# has an old Telegram message containing a button for a now-disabled feature.
def _fc_wrap_callback(fn, forced_key=None):
    if not fn or getattr(fn, '_fc_final_wrapped', False):
        return fn
    async def _wrapped(update, context, *args, **kwargs):
        q = getattr(update, 'callback_query', None)
        uid = q.from_user.id if q and q.from_user else (update.effective_user.id if update.effective_user else 0)
        key = forced_key or _fc_callback_key(q.data if q else '')
        if key and not _fc_enabled(uid, key):
            if q:
                try:
                    await q.answer(_fc_block_message(uid), show_alert=True)
                except Exception:
                    pass
            return True
        return await fn(update, context, *args, **kwargs)
    _wrapped._fc_final_wrapped = True
    return _wrapped

for _name, _key in (
    ('ai_chat_start', 'ai'), ('ai_chat_text', 'ai'),
    ('ai_chat_navigation_callback', 'ai'), ('price_callback', 'price_data'),
    ('support_callback', 'support'), ('vip_callback', 'vip'),
    ('settings_callback', 'settings'), ('goals_navigation_callback', 'goals'),
):
    try:
        if _name in globals():
            globals()[_name] = _fc_wrap_callback(globals()[_name], _key)
    except Exception:
        pass


# Universal guard for callback handlers registered after this patch. Existing
# named callbacks are wrapped above as well, so old and new routes are covered.
try:
    _fc_old_add_handler = Application.add_handler
    if not getattr(_fc_old_add_handler, '_fc_final_wrapped', False):
        def _fc_add_handler(self, handler, group=0):
            if isinstance(handler, CallbackQueryHandler) and not getattr(handler, '_fc_guarded', False):
                _old_cb = handler.callback
                async def _guarded(update, context, *args, **kwargs):
                    q = getattr(update, 'callback_query', None)
                    uid = q.from_user.id if q and q.from_user else (update.effective_user.id if update.effective_user else 0)
                    key = _fc_callback_key(q.data if q else '')
                    if key and not _fc_enabled(uid, key):
                        try:
                            await q.answer(_fc_block_message(uid), show_alert=True)
                        except Exception:
                            pass
                        return
                    return await _old_cb(update, context, *args, **kwargs)
                handler.callback = _guarded
                handler._fc_guarded = True
            return _fc_old_add_handler(self, handler, group)
        _fc_add_handler._fc_final_wrapped = True
        Application.add_handler = _fc_add_handler
except Exception:
    logger.exception('Could not install universal feature callback guard')


# Final text-flow guard. If a user disables a feature while they are inside its
# conversation, the next message is rejected and the flow is cleared.
try:
    _fc_old_text_router = text_router
    if not getattr(_fc_old_text_router, '_fc_final_wrapped', False):
        async def text_router(update, context):
            uid = update.effective_user.id if update.effective_user else 0
            text = (update.message.text or '').strip() if update.message else ''
            states = context.user_data if context and hasattr(context, 'user_data') else {}
            state_key = None
            if states.get('ai_chat'):
                state_key = 'ai'
            elif states.get('price_flow') or states.get('price_query'):
                state_key = 'price_data'
            elif states.get('customer_flow') or states.get('booking_flow'):
                state_key = 'customers'
            if state_key and not _fc_enabled(uid, state_key):
                states.pop('ai_chat', None)
                states.pop('price_flow', None)
                states.pop('price_query', None)
                states.pop('customer_flow', None)
                states.pop('booking_flow', None)
                await update.message.reply_text(_fc_block_message(uid), reply_markup=keyboard(uid))
                return True
            return await _fc_old_text_router(update, context)
        text_router._fc_final_wrapped = True
except Exception:
    logger.exception('Could not install final text feature guard')


# Clean, categorized admin switchboard. Every capability is under one category.
async def _fc_admin_categories(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if not _fc_admin(uid):
        return False
    rows = []
    for i, (title, items) in enumerate(FEATURE_CONTROL_CATEGORIES):
        rows.append([InlineKeyboardButton(title, callback_data=f'v25:fc:category:{i}')])
    rows.append([InlineKeyboardButton('⬅️ بازگشت', callback_data='v25:admin:hub')])
    q = getattr(update, 'callback_query', None)
    text = '⚙️ مدیریت قابلیت‌ها\n\nهر موضوع در دسته خودش قرار دارد. از داخل هر دسته قابلیت را فعال یا غیرفعال کن.'
    if q:
        try: await q.answer()
        except Exception: pass
        try: await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        except Exception: await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return True


async def _fc_admin_category(update, context, idx):
    uid = update.effective_user.id if update.effective_user else 0
    if not _fc_admin(uid):
        return False
    try:
        idx = int(idx)
        title, items = FEATURE_CONTROL_CATEGORIES[idx]
    except Exception:
        return await _fc_admin_categories(update, context)
    rows = []
    for key, label in items:
        try:
            enabled = _fc_enabled(uid, key)
        except Exception:
            enabled = True
        rows.append([InlineKeyboardButton(('🟢 ' if enabled else '🔴 ') + label, callback_data=f'v25:fc:toggle:{key}:{idx}')])
    rows.append([InlineKeyboardButton('⬅️ دسته‌ها', callback_data='v25:fc:categories')])
    q = getattr(update, 'callback_query', None)
    text = f'⚙️ {title}\n\n🟢 فعال   🔴 غیرفعال'
    if q:
        try: await q.answer()
        except Exception: pass
        try: await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        except Exception: await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return True


async def _fc_admin_router(update, context):
    q = getattr(update, 'callback_query', None)
    data = str(q.data or '') if q else ''
    uid = update.effective_user.id if update.effective_user else 0
    if not _fc_admin(uid):
        return False
    if data == 'v25:fc:categories':
        return await _fc_admin_categories(update, context)
    if data.startswith('v25:fc:category:'):
        return await _fc_admin_category(update, context, data.rsplit(':', 1)[1])
    if data.startswith('v25:fc:toggle:'):
        parts = data.split(':')
        key = parts[3] if len(parts) > 3 else ''
        if key:
            try:
                current = bool(feature_enabled(key)) if 'feature_enabled' in globals() else True
                set_feature(key, not current)
            except Exception:
                logger.exception('Final feature toggle failed: %s', key)
        idx = parts[4] if len(parts) > 4 else '0'
        return await _fc_admin_category(update, context, idx)
    return False

# Put the categorized switchboard first in the final v25 callback chain.
try:
    _fc_old_v25 = v25_callback
    if not getattr(_fc_old_v25, '_fc_final_wrapped', False):
        async def v25_callback(update, context):
            if await _fc_admin_router(update, context):
                return True
            return await _fc_old_v25(update, context)
        v25_callback._fc_final_wrapped = True
except Exception:
    pass
'''
    s += '\n\n' + code
    BOT.write_text(s, encoding='utf-8')
    print('Final feature control patch applied')
else:
    print('Final feature control patch already present')

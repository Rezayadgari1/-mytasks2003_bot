from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

if 'ADMIN_MANAGEMENT_ORGANIZER_V1' not in s:
    code = r'''# ADMIN_MANAGEMENT_ORGANIZER_V1
# Clean admin hub plus one complete feature switchboard.

ADMIN_FEATURE_CATALOG = [
    ('goals', '🎯 اهداف'),
    ('prepared_goals', '📋 اهداف آماده'),
    ('edit', '✏️ ویرایش'),
    ('weekly_table', '📊 جدول هفتگی'),
    ('stats', '📈 آمار'),
    ('profile', '👤 پروفایل'),
    ('achievements', '🏆 دستاوردها'),
    ('xp', '⭐ امتیاز XP'),
    ('referral', '👥 دعوت دوستان'),
    ('price_data', '💰 قیمت آنلاین'),
    ('ai', '🤖 هوش مصنوعی'),
    ('vip', '💎 VIP'),
    ('support', '🆘 پشتیبانی'),
    ('settings', '⚙️ تنظیمات'),
    ('unified_hub', '🧠 مرکز من'),
    ('portfolio', '💰 سرمایه‌های من'),
    ('installments', '💳 اقساط'),
    ('profile_sharing', '👤 اطلاعات من'),
    ('voice', '🎙️ دستیار صوتی'),
    ('calendar_hub', '📅 تقویم من'),
    ('business_services', '🏪 پنل کسب‌وکار'),
    ('important_reminders', '🔔 یادآوری‌های مهم'),
    ('customers', '👥 مشتریان'),
    ('customer_online_booking', '📅 رزرو آنلاین'),
    ('booking_payments', '💳 پرداخت رزرو'),
    ('surveys', '📝 نظرسنجی‌ها'),
    ('vip_plans', '💎 پلن‌های VIP'),
    ('business_finance', '💵 مالی کسب‌وکار'),
    ('sms', '📱 پیامک'),
    ('maintenance', '🛠 حالت تعمیرات'),
]

# Add every menu mapping known by the bot, without overwriting its existing labels.
try:
    for _label, _key in FEATURE_MENU_MAP.items():
        if _key and not any(k == _key for k, _ in ADMIN_FEATURE_CATALOG):
            ADMIN_FEATURE_CATALOG.append((_key, str(_label)))
except Exception:
    pass

async def _admin_features_panel(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if not admin_is_allowed(uid):
        return False
    rows=[]
    for key,label in ADMIN_FEATURE_CATALOG:
        try:
            enabled = bool(feature_enabled(key)) if 'feature_enabled' in globals() else True
        except Exception:
            enabled = True
        mark = '🟢' if enabled else '🔴'
        rows.append([InlineKeyboardButton(f'{mark} {label}', callback_data=f'v25:adminfeat:toggle:{key}')])
    rows.append([InlineKeyboardButton('🔄 بروزرسانی وضعیت', callback_data='v25:adminfeat:panel')])
    rows.append([InlineKeyboardButton('⬅️ بازگشت به مدیریت', callback_data='v25:admin:hub')])
    text='⚙️ مدیریت قابلیت‌ها\n\n🟢 فعال   🔴 غیرفعال\nبا انتخاب هر گزینه، وضعیت همان قابلیت تغییر می‌کند.'
    q=getattr(update,'callback_query',None)
    if q:
        try:
            await q.answer()
        except Exception: pass
        try:
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
        except Exception:
            await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return True

async def _admin_hub_panel(update, context):
    uid=update.effective_user.id if update.effective_user else 0
    if not admin_is_allowed(uid): return False
    rows=[
        [InlineKeyboardButton('⚙️ فعال / غیرفعال قابلیت‌ها', callback_data='v25:adminfeat:panel')],
        [InlineKeyboardButton('📢 مدیریت کانال‌ها', callback_data='v25:fsub:panel')],
        [InlineKeyboardButton('👥 مدیریت مدیران', callback_data='v25:admin:managers')],
        [InlineKeyboardButton('📨 پیام‌ها و اطلاع‌رسانی', callback_data='v25:admin:messages')],
        [InlineKeyboardButton('📊 گزارش و آمار', callback_data='v25:admin:reports')],
        [InlineKeyboardButton('⚙️ تنظیمات سیستم', callback_data='v25:admin:settings')],
    ]
    text='🛠 پنل مدیریت\n\nهمه تنظیمات مدیریتی از اینجا دسته‌بندی شده‌اند.'
    q=getattr(update,'callback_query',None)
    if q:
        try: await q.answer()
        except Exception: pass
        try: await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup(rows))
        except Exception: await q.message.reply_text(text,reply_markup=InlineKeyboardMarkup(rows))
    elif update.message:
        await update.message.reply_text(text,reply_markup=InlineKeyboardMarkup(rows))
    return True

async def _admin_feature_router(update, context):
    q=getattr(update,'callback_query',None)
    data=str(q.data or '') if q else ''
    uid=update.effective_user.id if update.effective_user else 0
    if not admin_is_allowed(uid): return False
    if data == 'v25:adminfeat:panel':
        return await _admin_features_panel(update,context)
    if data.startswith('v25:adminfeat:toggle:'):
        key=data.split('v25:adminfeat:toggle:',1)[1].strip()
        if key:
            try:
                current=bool(feature_enabled(key))
                set_feature(key, not current)
            except Exception:
                logger.exception('Admin feature toggle failed: %s',key)
        return await _admin_features_panel(update,context)
    if data == 'v25:admin:hub':
        return await _admin_hub_panel(update,context)
    return False

_old_v25_callback_admin = globals().get('v25_callback')
if _old_v25_callback_admin and not getattr(_old_v25_callback_admin,'_admin_router_wrapped',False):
    async def v25_callback(update,context):
        if await _admin_feature_router(update,context): return True
        return await _old_v25_callback_admin(update,context)
    v25_callback._admin_router_wrapped=True

_old_text_router_admin = globals().get('text_router')
if _old_text_router_admin and not getattr(_old_text_router_admin,'_admin_hub_wrapped',False):
    async def text_router(update,context):
        uid=update.effective_user.id if update.effective_user else 0
        text=(update.message.text or '').strip() if update.message else ''
        if admin_is_allowed(uid):
            if text in ('🛠 پنل مدیریت','🛠 مدیریت','مدیریت','Admin Panel'):
                await _admin_hub_panel(update,context); return True
            if text in ('⚙️ مدیریت قابلیت‌ها','فعال / غیرفعال قابلیت‌ها','مدیریت قابلیت‌ها'):
                await _admin_features_panel(update,context); return True
        return await _old_text_router_admin(update,context)
    text_router._admin_hub_wrapped=True

# Keep the admin reply keyboard compact and add one clear entry point.
_old_keyboard_admin = globals().get('keyboard')
if _old_keyboard_admin and not getattr(_old_keyboard_admin,'_admin_hub_wrapped',False):
    def keyboard(uid):
        kb=_old_keyboard_admin(uid)
        if not admin_is_allowed(uid): return kb
        rows=[list(r) for r in getattr(kb,'keyboard',[])]
        labels={str(x) for r in rows for x in r}
        if '🛠 پنل مدیریت' not in labels:
            rows.append(['🛠 پنل مدیریت'])
        return ReplyKeyboardMarkup(rows,resize_keyboard=True)
    keyboard._admin_hub_wrapped=True
'''
    s += '\n\n' + code
    BOT.write_text(s, encoding='utf-8')
    print('Admin management organizer applied')
else:
    print('Admin management organizer already present')

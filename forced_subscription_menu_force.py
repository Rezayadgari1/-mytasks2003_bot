from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')
MARK = 'FORCED_SUBSCRIPTION_MENU_FORCE_V3'

patch = r'''

# ================= FORCED SUBSCRIPTION MENU FORCE V3 =================
# FORCED_SUBSCRIPTION_MENU_FORCE_V3

# One canonical forced-subscription admin menu.
def _fsub_final_admin_keyboard(uid):
    fa = lang(uid) == 'fa'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel', callback_data='v25:fsub:add')],
        [InlineKeyboardButton('📋 لیست کانال‌های اجباری' if fa else '📋 Required channels', callback_data='v25:fsub:list')],
        [InlineKeyboardButton('🤖 ست‌کردن و بررسی ربات' if fa else '🤖 Set up / verify bot', callback_data='v25:fsub:setbot')],
        [InlineKeyboardButton('🟢 فعالسازی / 🔴 غیرفعال' if fa else '🟢 Enable / 🔴 Disable', callback_data='v25:fsub:toggle')],
        [InlineKeyboardButton('🗑 حذف کانال' if fa else '🗑 Remove channel', callback_data='v25:fsub:delete_list')],
        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin:hub')],
    ])

_q_fsub_admin_keyboard = _fsub_final_admin_keyboard
_fsub_admin_keyboard = _fsub_final_admin_keyboard

async def _fsub_show_canonical_menu(update, context):
    uid = update.effective_user.id if update.effective_user else 0
    if not _fsub_admin(uid):
        q = getattr(update, 'callback_query', None)
        if q:
            await q.answer('دسترسی ندارید.', show_alert=True)
        return True
    text = _fsub_admin_text(uid)
    kb = _fsub_final_admin_keyboard(uid)
    q = getattr(update, 'callback_query', None)
    if q:
        await q.answer()
        try:
            await q.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception:
            await q.message.reply_text(text, parse_mode='HTML', reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode='HTML', reply_markup=kb)
    return True

# The screenshot is produced by the legacy text entry, not only v25_callback.
_old_text_router_fsub_v3 = globals().get('text_router')
if _old_text_router_fsub_v3 and not getattr(_old_text_router_fsub_v3, '_fsub_v3_wrapped', False):
    async def text_router(update, context):
        uid = update.effective_user.id if update.effective_user else 0
        txt = (update.message.text or '').strip() if update.message else ''
        if _fsub_admin(uid) and txt in ('🔒 عضویت اجباری', 'عضویت اجباری', '🔐 عضویت اجباری', 'عضویت اجباری 🔒'):
            return await _fsub_show_canonical_menu(update, context)
        return await _old_text_router_fsub_v3(update, context)
    text_router._fsub_v3_wrapped = True

# Also catch the legacy callback entry before older callback handlers can render the old menu.
_old_add_handler_fsub_v3 = getattr(Application, 'add_handler', None)
if _old_add_handler_fsub_v3 and not getattr(Application.add_handler, '_fsub_v3_installed', False):
    async def _fsub_legacy_callback(update, context):
        q = getattr(update, 'callback_query', None)
        data = str(getattr(q, 'data', '') or '') if q else ''
        uid = update.effective_user.id if update.effective_user else 0
        low = data.lower()
        panel_aliases = {
            'v25:fsub:panel', 'forcedsub:home', 'fsub:panel',
            'forcedsub:panel', 'forced_subscription:panel',
            'admin:forcedsub', 'admin:forced_subscription',
            'channel:forcedsub', 'channel:forced_subscription',
        }
        is_panel = data in panel_aliases or ('fsub' in low and ('panel' in low or 'home' in low)) or ('forcedsub' in low and ('panel' in low or 'home' in low))
        if is_panel:
            return await _fsub_show_canonical_menu(update, context)
        return False

    _fsub_legacy_handler = CallbackQueryHandler(_fsub_legacy_callback, pattern=r'(?i).*')

    def _fsub_add_handler(self, handler, group=0, *args, **kwargs):
        if not getattr(self, '_fsub_v3_interceptor_added', False):
            _old_add_handler_fsub_v3(self, _fsub_legacy_handler, group=-100)
            self._fsub_v3_interceptor_added = True
        return _old_add_handler_fsub_v3(self, handler, group=group, *args, **kwargs)

    _fsub_add_handler._fsub_v3_installed = True
    Application.add_handler = _fsub_add_handler

'''

if MARK not in s:
    s += patch

BOT.write_text(s, encoding='utf-8')
print('Forced subscription legacy entry point intercepted')
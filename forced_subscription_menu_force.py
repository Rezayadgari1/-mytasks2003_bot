from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')
MARK = 'FORCED_SUBSCRIPTION_MENU_FORCE_V2'

# The screenshot uses the legacy quality panel, which calls _q_fsub_admin_keyboard.
# Override that exact function name after all earlier patches have been applied.
patch = r'''

# ================= FORCED SUBSCRIPTION MENU FORCE V2 =================
# FORCED_SUBSCRIPTION_MENU_FORCE_V2

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

_old_v25_callback_force_v2 = globals().get('v25_callback')
if _old_v25_callback_force_v2:
    async def v25_callback(update, context):
        q = getattr(update, 'callback_query', None)
        data = str(getattr(q, 'data', '') or '') if q else ''
        uid = update.effective_user.id if update.effective_user else 0
        if data in ('v25:fsub:panel', 'forcedsub:home'):
            if not _fsub_admin(uid):
                await q.answer('دسترسی ندارید.', show_alert=True)
                return True
            await q.answer()
            await q.message.edit_text(_fsub_admin_text(uid), parse_mode='HTML', reply_markup=_fsub_final_admin_keyboard(uid))
            return True
        if data == 'v25:fsub:setbot':
            return await _fsub_force_setbot(update, context)
        if data == 'v25:fsub:delete_list':
            if not _fsub_admin(uid):
                await q.answer('دسترسی ندارید.', show_alert=True)
                return True
            rows = _fsub_channels(True)
            buttons = [[InlineKeyboardButton('🗑 ' + str(r['title'] or r['channel_id']), callback_data=f'v25:fsub:remove:{r["id"]}')] for r in rows]
            buttons.append([InlineKeyboardButton('⬅️ بازگشت', callback_data='v25:fsub:home')])
            await q.answer()
            await q.message.edit_text('🗑 حذف کانال اجباری\n\nکانال موردنظر را انتخاب کن.', reply_markup=InlineKeyboardMarkup(buttons))
            return True
        if data == 'v25:fsub:home':
            if _fsub_admin(uid):
                await q.answer()
                await q.message.edit_text(_fsub_admin_text(uid), parse_mode='HTML', reply_markup=_fsub_final_admin_keyboard(uid))
            else:
                await q.answer('دسترسی ندارید.', show_alert=True)
            return True
        if data.startswith('v25:fsub:remove:'):
            try:
                c = db()
                c.execute('DELETE FROM managed_channels WHERE id=?', (int(data.rsplit(':', 1)[1]),))
                c.commit()
                c.close()
                await q.answer('کانال حذف شد.')
                await _fsub_list_callback(update, context)
            except Exception:
                logger.exception('forced subscription channel removal failed')
                await q.answer('خطا در حذف کانال.', show_alert=True)
            return True
        return await _old_v25_callback_force_v2(update, context)
'''

if MARK not in s:
    s += patch

BOT.write_text(s, encoding='utf-8')
print('Forced subscription final menu V2 applied')
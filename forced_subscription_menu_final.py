from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')
MARK = 'FORCED_SUBSCRIPTION_MENU_FINAL_V3'

if MARK not in s:
    patch = r'''

# ================= FORCED SUBSCRIPTION ADMIN MENU FINAL V3 =================
# FORCED_SUBSCRIPTION_MENU_FINAL_V3
# Single final menu definition. This replaces the old scattered forced-sub menu.

async def _fsub_remove_channel_callback(update, context, row_id):
    q = update.callback_query
    uid = q.from_user.id
    if not _fsub_admin(uid):
        await q.answer('دسترسی ندارید.', show_alert=True)
        return
    c = db()
    try:
        row = c.execute('SELECT id,title,channel_id FROM managed_channels WHERE id=?', (int(row_id),)).fetchone()
        if not row:
            await q.answer('کانال پیدا نشد.', show_alert=True)
            return
        c.execute('DELETE FROM managed_channels WHERE id=?', (int(row_id),))
        c.commit()
    finally:
        c.close()
    await q.answer('کانال حذف شد.')
    await _fsub_list_callback(update, context)


async def _fsub_delete_list_callback(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not _fsub_admin(uid):
        await q.answer('دسترسی ندارید.', show_alert=True)
        return
    rows = _fsub_channels(True)
    fa = lang(uid) == 'fa'
    buttons = []
    for r in rows:
        buttons.append([InlineKeyboardButton(
            ('🗑 ' if fa else '🗑 ') + str(r['title'] or r['channel_id']),
            callback_data=f"v25:fsub:remove:{r['id']}"
        )])
    buttons.append([InlineKeyboardButton('⬅️ بازگشت' if fa else '⬅️ Back', callback_data='v25:fsub:home')])
    text = '🗑 <b>حذف کانال اجباری</b>\n\nکانالی را برای حذف انتخاب کن.' if fa else '🗑 <b>Remove required channel</b>\n\nSelect a channel to remove.'
    if not rows:
        text += '\n\nهنوز کانالی ثبت نشده.' if fa else '\n\nNo channels configured.'
    await q.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons))


def _fsub_admin_keyboard(uid):
    fa = lang(uid) == 'fa'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel', callback_data='v25:fsub:add')],
        [InlineKeyboardButton('📋 لیست کانال‌های اجباری' if fa else '📋 Required channels', callback_data='v25:fsub:list')],
        [InlineKeyboardButton('🤖 ست‌کردن و بررسی ربات' if fa else '🤖 Set up / verify bot', callback_data='v25:fsub:setbot')],
        [InlineKeyboardButton('🟢 فعالسازی / 🔴 غیرفعال' if fa else '🟢 Enable / 🔴 Disable', callback_data='v25:fsub:toggle')],
        [InlineKeyboardButton('🗑 حذف کانال' if fa else '🗑 Remove channel', callback_data='v25:fsub:delete_list')],
        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin:hub')],
    ])


async def _fsub_admin_show_final(update, context):
    uid = update.effective_user.id
    if not _fsub_admin(uid):
        return
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if not target:
        return
    text = _fsub_admin_text(uid)
    kb = _fsub_admin_keyboard(uid)
    if update.callback_query:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.reply_text(text, reply_markup=kb)


_old_v25_callback_fsub_menu_final = globals().get('v25_callback')
if _old_v25_callback_fsub_menu_final:
    async def v25_callback(update, context):
        q = getattr(update, 'callback_query', None)
        data = str(getattr(q, 'data', '') or '') if q else ''
        uid = update.effective_user.id if update.effective_user else 0
        if data == 'v25:fsub:delete_list':
            await _fsub_delete_list_callback(update, context)
            return True
        if data.startswith('v25:fsub:remove:'):
            try:
                await _fsub_remove_channel_callback(update, context, data.rsplit(':', 1)[1])
            except Exception:
                logger.exception('Forced subscription remove failed')
                if q:
                    await q.answer('خطا در حذف کانال.', show_alert=True)
            return True
        if data == 'v25:fsub:home':
            if not _fsub_admin(uid):
                if q:
                    await q.answer('دسترسی ندارید.', show_alert=True)
                return True
            await _fsub_admin_show_final(update, context)
            return True
        return await _old_v25_callback_fsub_menu_final(update, context)
'''
    s += patch
    BOT.write_text(s, encoding='utf-8')
    print('Forced subscription final menu applied')
else:
    print('v25_callback not found, menu patch not applied')

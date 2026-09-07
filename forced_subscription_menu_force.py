from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')
MARK = 'FORCED_SUBSCRIPTION_MENU_FORCE_V1'

# Replace every known old admin keyboard definition at build time.
old_blocks = [
"""def _fsub_admin_keyboard(uid):\n    fa = lang(uid) == 'fa'\n    return InlineKeyboardMarkup([\n        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel', callback_data='v25:fsub:add')],\n        [InlineKeyboardButton('📋 کانال‌های اجباری' if fa else '📋 Required channels', callback_data='v25:fsub:list')],\n        [InlineKeyboardButton('🔄 وضعیت عضویت اجباری' if fa else '🔄 Forced subscription status', callback_data='v25:fsub:toggle')],\n        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin')],\n    ])""",
]
new_block = """def _fsub_admin_keyboard(uid):
    fa = lang(uid) == 'fa'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ افزودن کانال اجباری' if fa else '➕ Add required channel', callback_data='v25:fsub:add')],
        [InlineKeyboardButton('📋 لیست کانال‌های اجباری' if fa else '📋 Required channels', callback_data='v25:fsub:list')],
        [InlineKeyboardButton('🤖 ست‌کردن و بررسی ربات' if fa else '🤖 Set up / verify bot', callback_data='v25:fsub:setbot')],
        [InlineKeyboardButton('🟢 فعالسازی / 🔴 غیرفعال' if fa else '🟢 Enable / 🔴 Disable', callback_data='v25:fsub:toggle')],
        [InlineKeyboardButton('🗑 حذف کانال' if fa else '🗑 Remove channel', callback_data='v25:fsub:delete_list')],
        [InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin:hub')],
    ])"""
for old in old_blocks:
    s = s.replace(old, new_block)

# Keep legacy back callback compatible everywhere in the generated source.
s = s.replace("callback_data='v25:admin')", "callback_data='v25:admin:hub')")

# Add a final runtime router only once. It delegates all unrelated callbacks.
if MARK not in s:
    patch = r'''

# ================= FORCED SUBSCRIPTION MENU FORCE V1 =================
# FORCED_SUBSCRIPTION_MENU_FORCE_V1

async def _fsub_force_setbot(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not _fsub_admin(uid):
        await q.answer('دسترسی ندارید.', show_alert=True)
        return True
    try:
        me = await context.bot.get_me()
        rows = _fsub_channels(True)
        if not rows:
            text = '🤖 ربات فعال است، اما هنوز کانالی برای بررسی ثبت نشده است.\n\nاول «➕ افزودن کانال اجباری» را بزن.'
        else:
            checks = []
            for r in rows:
                ok = await _fsub_verify_bot_admin(context.bot, r['channel_id'])
                checks.append(('🟢' if ok else '🔴') + ' ' + str(r['title'] or r['channel_id']))
            text = '🤖 وضعیت ربات در کانال‌های اجباری:\n\n' + '\n'.join(checks) + '\n\nربات فعال: @' + str(getattr(me, 'username', '') or me.id)
        await q.answer()
        await q.message.edit_text(text, reply_markup=_fsub_admin_keyboard(uid))
    except Exception as exc:
        logger.exception('forced subscription bot setup check failed: %s', exc)
        await q.answer('خطا در بررسی ربات.', show_alert=True)
    return True


_old_v25_callback_force_menu = globals().get('v25_callback')
if _old_v25_callback_force_menu:
    async def v25_callback(update, context):
        q = getattr(update, 'callback_query', None)
        data = str(getattr(q, 'data', '') or '') if q else ''
        uid = update.effective_user.id if update.effective_user else 0
        if data == 'v25:fsub:setbot':
            return await _fsub_force_setbot(update, context)
        if data == 'v25:fsub:delete_list':
            if not _fsub_admin(uid):
                await q.answer('دسترسی ندارید.', show_alert=True)
                return True
            rows = _fsub_channels(True)
            buttons = [[InlineKeyboardButton('🗑 ' + str(r['title'] or r['channel_id']), callback_data=f"v25:fsub:remove:{r['id']}")] for r in rows]
            buttons.append([InlineKeyboardButton('⬅️ بازگشت', callback_data='v25:fsub:home')])
            await q.answer()
            await q.message.edit_text('🗑 حذف کانال اجباری\n\nکانال موردنظر را انتخاب کن.', reply_markup=InlineKeyboardMarkup(buttons))
            return True
        if data == 'v25:fsub:home':
            if _fsub_admin(uid):
                await q.answer()
                await q.message.edit_text(_fsub_admin_text(uid), reply_markup=_fsub_admin_keyboard(uid))
            else:
                await q.answer('دسترسی ندارید.', show_alert=True)
            return True
        if data.startswith('v25:fsub:remove:'):
            row_id = data.rsplit(':', 1)[1]
            try:
                c = db()
                c.execute('DELETE FROM managed_channels WHERE id=?', (int(row_id),))
                c.commit()
                c.close()
                await q.answer('کانال حذف شد.')
                await _fsub_list_callback(update, context)
            except Exception:
                logger.exception('forced subscription channel removal failed')
                await q.answer('خطا در حذف کانال.', show_alert=True)
            return True
        return await _old_v25_callback_force_menu(update, context)
'''
    s += patch

BOT.write_text(s, encoding='utf-8')
print('Forced subscription menu force applied')
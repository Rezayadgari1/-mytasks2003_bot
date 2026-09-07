from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

MARK = 'NAVIGATION_BACK_FIX_V1'

# Repair the actual generated forced-subscription button before bot.py is imported.
old_button = "InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin')"
new_button = "InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin:hub')"
if old_button in s:
    s = s.replace(old_button, new_button)

if MARK not in s:
    patch = r'''

# ===================== NAVIGATION BACK FIX V1 =====================
# NAVIGATION_BACK_FIX_V1
# Keep legacy admin/forced-sub Back callbacks compatible with the organized hub.

_old_v25_callback_backfix = globals().get('v25_callback')
if _old_v25_callback_backfix and not getattr(_old_v25_callback_backfix, '_nav_backfix_wrapped', False):
    async def v25_callback(update, context):
        q = getattr(update, 'callback_query', None)
        data = str(getattr(q, 'data', '') or '') if q else ''
        uid = update.effective_user.id if update.effective_user else 0
        if data in ('v25:admin', 'v25:fsub:back'):
            try:
                if not admin_is_allowed(uid):
                    if q:
                        await q.answer('دسترسی ندارید.', show_alert=True)
                    return True
            except Exception:
                pass
            try:
                await _admin_hub_panel(update, context)
                return True
            except Exception:
                logger.exception('Admin back navigation failed')
                if q:
                    try:
                        await q.answer('خطا در بازگشت', show_alert=True)
                    except Exception:
                        pass
                return True
        return await _old_v25_callback_backfix(update, context)
    v25_callback._nav_backfix_wrapped = True

'''
    s += patch

BOT.write_text(s, encoding='utf-8')
print('Navigation back fix applied')
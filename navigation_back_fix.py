from pathlib import Path

BOT = Path('/app/bot.py')
s = BOT.read_text(encoding='utf-8')

MARK = 'NAVIGATION_BACK_FIX_V1'
if MARK not in s:
    patch = r'''

# ===================== NAVIGATION BACK FIX V1 =====================
# NAVIGATION_BACK_FIX_V1
# Repair legacy admin/forced-sub callbacks so Back always reaches the admin hub.

# The forced-subscription screen used the legacy callback `v25:admin`, while the
# organized admin hub uses `v25:admin:hub`. Keep both valid for compatibility.
try:
    s = None
except Exception:
    pass

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

# Repair the button itself in the already-generated forced-subscription screen.
s = globals().get('s')
if isinstance(s, str):
    s2 = s.replace(
        "InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin')",
        "InlineKeyboardButton('⬅️ منوی مدیریت' if fa else '⬅️ Admin menu', callback_data='v25:admin:hub')"
    )
    if s2 != s:
        BOT.write_text(s2, encoding='utf-8')
        print('Navigation back button repaired')
'''
    # The patch needs access to the current source text. Inject it directly into the file.
    # Keep the transformation additive and idempotent.
    patch = patch.replace("try:\n    s = None\nexcept Exception:\n    pass\n", "")
    s += '\n\n' + patch
    BOT.write_text(s, encoding='utf-8')
    print('Navigation back fix applied')
else:
    print('Navigation back fix already present')
